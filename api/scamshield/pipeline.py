"""The analyze pipeline: normalize -> verdict cache -> redact -> triage -> memory -> evidence -> verdict -> persist.

Every step appends to a per-message ``Trace`` (model, tokens, latency, cost at the listed Token Factory prices)
stored with the result. A repeat of the same message (same parent profile, same models) is answered from the
verdict cache for free; the budget guard (``budget.py``) can downgrade or refuse the paid steps.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from . import prompts
from .budget import KILLED, NORMAL, Budget
from .config import TIERS, Settings, model_key
from .llm import LLMClient, LLMError, ModelRouter
from .memory import MemoryStore, Profile, compare_to_contacts, service_owns_all_links
from .models import (
    AnalysisResult,
    ContactMatch,
    Lookup,
    Message,
    RawMessage,
    RedFlag,
    Trace,
    TraceStep,
    Triage,
    Verdict,
    label_for,
)
from .normalize import domain_of, normalize
from .planning import candidates_for, default_plan, validate_plan
from .redact import redact
from .search.base import SearchClient
from .skills import SKILLS, EvidenceDesk

SNIPPET_CHARS = 300
EXCERPT_CHARS = 1200


class Pipeline:
    def __init__(self, settings: Settings, llm: LLMClient, search: SearchClient, store: MemoryStore):
        self.settings = settings
        self.llm = llm
        self.search = search
        self.store = store
        self.budget = Budget(store, settings)
        self.router = ModelRouter(llm, settings, self.budget)

    def use_store(self, store: MemoryStore) -> None:
        """Swap the database (the eval runs on a fresh in-memory one); the budget ledger moves with it."""
        self.store = store
        self.budget.store = store

    def cache_key(self, message: Message, profile: Profile) -> str:
        """Verdict cache key: the message hash plus everything that changes the answer (who it is for, what the
        profile says, which models and clients produced it)."""
        parts = [
            profile.id,
            hashlib.sha256(profile.model_dump_json().encode()).hexdigest(),
            message.sender or "",
            message.subject or "",
            " ".join(message.body.split()),
            self.llm.name,
            self.search.name,
            *(model_key(self.settings.model_for(t)) for t in TIERS),
        ]
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    # ------------------------------------------------------------------------------------------------
    def analyze(self, raw: RawMessage, parent_id: str, *, use_cache: bool = True, pin: bool = False) -> AnalysisResult:
        """Analyze one message. ``use_cache=False`` forces a fresh run; ``pin=True`` stores the verdict with no
        expiry (``scamshield warm`` does this for the seed messages so the demo costs nothing)."""
        profile = self.store.get_profile(parent_id) or Profile(id=parent_id, name=parent_id)
        trace = Trace()

        with _timed(trace, "normalize") as step:
            message = normalize(raw)
            step.detail = f"channel={message.channel} urls={len(message.urls)} phones={len(message.phones)}"

        key = self.cache_key(message, profile)
        if use_cache and (cached := self.store.verdict_cache_get(key, self.settings.verdict_cache_ttl_seconds)):
            return self._serve_cached(cached, message, trace, profile, parent_id)

        mode = self.budget.status()
        if mode.mode != NORMAL:
            trace.steps.append(TraceStep(name="budget", detail=f"{mode.mode}: {mode.reason}"))

        with _timed(trace, "redact") as step:
            redacted = redact(message.body)
            step.detail = f"redacted {redacted.total} item(s) {redacted.counts}" if redacted.total else "nothing to redact"

        triage = self._triage(message, redacted.text, trace)
        triage_ok = not any(s.name == "triage" and s.error for s in trace.steps)

        with _timed(trace, "memory") as step:
            contact = compare_to_contacts(profile, message)
            step.detail = f"{contact.status}: {contact.reason}"

        lookups: list[Lookup] = []
        fast = self._fast_path_note(triage, contact, profile, message)
        if fast:
            verdict = self._fast_verdict(triage, contact, fast)
            trace.steps.append(TraceStep(name="fast_path", detail=f"{fast}: skipped evidence and verdict model"))
        else:
            if triage.risk != "none":
                lookups = self._gather_evidence(message, redacted.text, triage, contact, trace)
            verdict = self._verdict(message, redacted.text, triage, contact, lookups, profile, trace, triage_ok)

        result = AnalysisResult(
            id=uuid.uuid4().hex[:12],
            parent_id=parent_id,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            message=message,
            triage=triage,
            contact=contact,
            lookups=lookups,
            verdict=verdict,
            trace=trace,
            offline=self.llm.offline or self.search.offline,
            budget_mode=mode.mode,
        )
        self.store.save_analysis(result, keep_body=profile.store_green_bodies)
        # Only a clean, full-strength run is worth replaying: never cache a degraded or failed answer.
        if mode.mode == NORMAL and not verdict.degraded and not any(s.error for s in trace.steps):
            self.store.verdict_cache_put(key, _cacheable(result, profile), pinned=pin)
        return result

    def _serve_cached(
        self, cached: AnalysisResult, message: Message, trace: Trace, profile: Profile, parent_id: str
    ) -> AnalysisResult:
        trace.steps.append(
            TraceStep(
                name="verdict_cache",
                detail=f"hit: verdict of {cached.id} replayed, no model or search call (original run cost "
                f"${cached.trace.total_cost_usd:.5f})",
            )
        )
        result = cached.model_copy(
            update={
                "id": uuid.uuid4().hex[:12],
                "parent_id": parent_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": message,
                "trace": trace,
                "cached_from": cached.id,
                "budget_mode": self.budget.mode,
            }
        )
        self.store.save_analysis(result, keep_body=profile.store_green_bodies)
        return result

    # --- triage -----------------------------------------------------------------------------------
    def _triage(self, message: Message, body: str, trace: Trace) -> Triage:
        try:
            resp = self.router.call(
                "triage",
                prompts.TRIAGE_SYSTEM,
                prompts.triage_user(body, message.sender, message.channel, message.urls, message.phones),
                {"message": message.model_dump()},
                trace,
            )
            return Triage.model_validate(_clean_triage(resp.data))
        except (LLMError, ValidationError) as exc:
            _mark_error(trace, "triage", exc)
            # Fail toward caution: unknown risk is treated as medium so evidence and verdict still run.
            return Triage(risk="medium", summary="triage unavailable; treated as medium risk")

    def _fast_path_note(self, triage: Triage, contact: ContactMatch, profile: Profile, message: Message) -> str | None:
        sig = triage.signals
        if triage.risk == "none" and not contact.lookalike_of:
            return "no risk signals"
        if contact.status == "trusted" and not (sig.money_request or sig.secrecy_request):
            return f"trusted contact {contact.name}"
        if (
            contact.status == "known_service"
            and not sig.money_request
            and service_owns_all_links(profile, contact, message)
        ):
            return f"known service {contact.name}, links on its own domain"
        return None

    def _fast_verdict(self, triage: Triage, contact: ContactMatch, why: str) -> Verdict:
        score = 5 if contact.status != "unknown" else 10
        return Verdict(
            label=label_for(score),
            score=score,
            red_flags=[],
            safe_action="This looks fine. No action needed.",
            family_script="",
            evidence_note=f"Not checked online ({why}).",
        )

    # --- evidence ---------------------------------------------------------------------------------
    def _gather_evidence(
        self, message: Message, body: str, triage: Triage, contact: ContactMatch, trace: Trace
    ) -> list[Lookup]:
        limit = self.settings.max_search_calls
        candidates = candidates_for(message.urls, message.phones, message.sender, contact.status != "unknown")
        plan: list[dict] = []
        if candidates["urls"] or candidates["phones"] or triage.key_phrase:
            try:
                resp = self.router.call(
                    "plan",
                    prompts.PLAN_SYSTEM,
                    prompts.plan_user(body, triage.model_dump(), candidates),
                    {"message": message.model_dump(), "triage": triage.model_dump(), "candidates": candidates},
                    trace,
                )
                plan = validate_plan(resp.data, candidates, message.body, limit)
            except LLMError as exc:
                _mark_error(trace, "plan", exc)
            if not plan:  # planner failed or proposed nothing usable
                plan = validate_plan({"lookups": default_plan(candidates, triage.key_phrase)}, candidates, message.body, limit)

        desk = EvidenceDesk(self.search, self.store, self.settings, trace, self.budget)
        lookups = [SKILLS[item["skill"]](desk, item["value"]) for item in plan]
        self._extract_top_source(desk, lookups, message)
        return lookups

    def _extract_top_source(self, desk: EvidenceDesk, lookups: list[Lookup], message: Message) -> None:
        """Spend the one allowed Tavily extract on the best evidence page, so the verdict reads more than a snippet.
        Only pages that a search returned are read; a page on a domain the message itself links to never is."""
        if desk.extract_left <= 0:
            return
        message_hosts = {domain_of(u) for u in message.urls}
        for lk in lookups:
            for hit in lk.hits:
                if domain_of(hit.url) in message_hosts:
                    continue
                text = desk.extract(hit.url)
                if len(text) > len(hit.content):
                    hit.excerpt = text
                return

    # --- verdict ----------------------------------------------------------------------------------
    def _verdict(
        self,
        message: Message,
        body: str,
        triage: Triage,
        contact: ContactMatch,
        lookups: list[Lookup],
        profile: Profile,
        trace: Trace,
        triage_ok: bool = True,
    ) -> Verdict:
        evidence = [
            {
                "skill": lk.skill,
                "value": lk.value,
                "reports": [
                    {
                        "title": h.title,
                        "url": h.url,
                        "snippet": h.content[:SNIPPET_CHARS],
                        **({"page_text": h.excerpt[:EXCERPT_CHARS]} if h.excerpt else {}),
                    }
                    for h in lk.hits
                ],
            }
            for lk in lookups
        ]
        context = {
            "message": message.model_dump(),
            "triage": triage.model_dump(),
            "contact": contact.model_dump(),
            "lookups": [lk.model_dump() for lk in lookups],
        }
        degraded = False
        try:
            resp = self.router.call(
                "verdict",
                prompts.VERDICT_SYSTEM,
                prompts.verdict_user(body, triage.model_dump(), contact.model_dump(), evidence),
                context,
                trace,
            )
            data = resp.data
            score = int(max(0, min(100, round(float(data["score"])))))
            flags = _parse_flags(data.get("red_flags"))
            safe_action = str(data.get("safe_action") or "")
            family_script = str(data.get("family_script") or "")
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            _mark_error(trace, "verdict", exc)
            degraded = True
            score = 15 if contact.status == "trusted" else 50
            flags = []
            safe_action = "We could not fully check this message. Don't reply, click or pay; ask your family first."
            family_script = "Hi Mom, I'm checking a message you got. Please don't reply or click anything until we talk."

        # Only URLs that Tavily actually returned for this message may be cited as evidence.
        allowed = {h.url for lk in lookups for h in lk.hits}
        stripped = sum(1 for f in flags if f.evidence_url and f.evidence_url not in allowed)
        flags = [RedFlag(text=f.text, evidence_url=f.evidence_url if f.evidence_url in allowed else None) for f in flags]

        if contact.lookalike_of and contact.status != "trusted":
            flags.insert(0, RedFlag(text=f"The address looks like {contact.lookalike_of}'s, but it is not the real one."))
        score, note = apply_memory_guardrails(score, triage, contact, message, profile)
        details = []
        if note:
            details.append(note)
        if not triage_ok and score < 30:
            score = 30  # never call a message safe when its triage step failed
            details.append("triage failed: floor 30")
        label = label_for(score)
        if stripped:
            details.append(f"removed {stripped} evidence url(s) not returned by search")
        trace.steps.append(TraceStep(name="guardrails", detail="; ".join(details) or "no adjustments"))

        return Verdict(
            label=label,
            score=score,
            red_flags=flags,
            safe_action=safe_action,
            family_script=family_script if label != "green" else "",
            evidence_note=evidence_note(lookups),
            degraded=degraded,
        )


def apply_memory_guardrails(
    score: int, triage: Triage, contact: ContactMatch, message: Message, profile: Profile
) -> tuple[int, str | None]:
    """Deterministic floors/caps so memory always counts, whatever the model said."""
    sig = triage.signals
    asks = sig.money_request or sig.secrecy_request
    if contact.lookalike_of and contact.status != "trusted":
        return max(score, 75), f"imitates {contact.lookalike_of}'s address: floor 75"
    if contact.status == "trusted":
        if asks:
            return max(score, 30), "trusted contact but asks for money/secrecy: at least 'careful'"
        return min(score, 25), f"trusted contact {contact.name}: capped at 25"
    if contact.status == "known_service" and not sig.money_request and service_owns_all_links(profile, contact, message):
        return min(score, 25), f"known service {contact.name}: capped at 25"
    if contact.status == "unknown":
        if sig.impersonation == "family" and asks:
            return max(score, 75), "unknown sender claims to be family and asks for money/secrecy: floor 75"
        if sig.money_request:
            return max(score, 50), "unknown sender asks for money: floor 50"
    return score, None


def evidence_note(lookups: list[Lookup]) -> str:
    if not lookups:
        return "No online lookups were run."
    found = [lk for lk in lookups if lk.hits]
    if not found:
        failed = [lk for lk in lookups if lk.skipped]
        if len(failed) == len(lookups):
            return "Online lookups could not be completed."
        return "No public reports found."
    urls = {h.url for lk in found for h in lk.hits}
    return f"Public reports found: {len(urls)} source(s)."


def _parse_flags(raw: object) -> list[RedFlag]:
    flags: list[RedFlag] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            flags.append(RedFlag(text=item))
        elif isinstance(item, dict) and item.get("text"):
            url = item.get("evidence_url")
            flags.append(RedFlag(text=str(item["text"]), evidence_url=url if isinstance(url, str) and url else None))
    return flags


def _cacheable(result: AnalysisResult, profile: Profile) -> AnalysisResult:
    """The result as stored in the verdict cache: same privacy rule as the analyses table (a 🟢 body is dropped)."""
    if result.verdict.label == "green" and not profile.store_green_bodies:
        result = result.model_copy(deep=True)
        result.message.body = ""
        result.message.subject = None
    return result


def _clean_triage(data: dict) -> dict:
    allowed = set(Triage.model_fields)
    return {k: v for k, v in data.items() if k in allowed}


def _mark_error(trace: Trace, step: str, exc: Exception) -> None:
    """Attach an error to the step's trace entry (the router already did for client failures)."""
    for s in reversed(trace.steps):
        if s.name == step:
            s.error = s.error or str(exc)
            return
    trace.steps.append(TraceStep(name=step, kind="model", error=str(exc)))


class _timed:
    def __init__(self, trace: Trace, name: str):
        self.trace, self.name = trace, name

    def __enter__(self) -> TraceStep:
        self.step = TraceStep(name=self.name, kind="local")
        self._start = time.perf_counter()
        return self.step

    def __exit__(self, *exc) -> None:
        self.step.latency_ms = round((time.perf_counter() - self._start) * 1000, 2)
        self.trace.steps.append(self.step)
