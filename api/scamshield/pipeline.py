"""The analyze pipeline: normalize -> verdict cache -> redact -> memory -> quick check -> triage + evidence (at the
same time) -> verdict -> persist.

Every step appends to a per-message ``Trace`` (model, tokens, latency, cost at the listed Token Factory prices)
stored with the result. A repeat of the same message (same parent profile, same models) is answered from the
verdict cache for free; the budget guard (``budget.py``) can downgrade or refuse the paid steps.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait
from concurrent.futures import TimeoutError as FutureTimeout
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
from .quick import QuickAnswer, quick_check
from .redact import redact
from .search.base import SearchClient
from .skills import SKILLS, EvidenceDesk

SNIPPET_CHARS = 300
EXTRACT_GRACE_SECONDS = 1.5  # how long the verdict waits for the report page once every lookup is in
# Hosts whose pages Tavily extract cannot read (login walls): an extract there only costs time.
NO_EXTRACT_HOSTS = ("facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "youtube.com", "linkedin.com")
EXCERPT_CHARS = 1200
MAX_FAMILY_POINTS = 4
VERDICT_FORMAT = "family-points"  # part of the verdict cache key: a cached verdict in an older shape is not replayed


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
            VERDICT_FORMAT,
        ]
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    # ------------------------------------------------------------------------------------------------
    def analyze(self, raw: RawMessage, parent_id: str, *, use_cache: bool = True, pin: bool = False) -> AnalysisResult:
        """Analyze one message. ``use_cache=False`` forces a fresh run; ``pin=True`` stores the verdict with no
        expiry (``scamshield warm`` does this for the seed messages so the demo costs nothing)."""
        for event in self.analyze_steps(raw, parent_id, use_cache=use_cache, pin=pin):
            if event["type"] == "result":
                return event["result"]
        raise RuntimeError("the analysis ended without a result")

    def analyze_steps(
        self, raw: RawMessage, parent_id: str, *, use_cache: bool = True, pin: bool = False
    ) -> Iterator[dict]:
        """``analyze`` as a stream of progress events, for the parent screen to show while it works:

        * ``{"type": "step", "step": "reading" | "searching" | "deciding"}``
        * ``{"type": "step", "step": "found", "count": n}``: distinct public reports the lookups found
        * ``{"type": "quick", "label": "red" | "green", "reason": str}``: an instant preview (``quick.py``)
        * ``{"type": "result", "result": AnalysisResult}``: always last

        Triage runs at the same time as the lookups that do not need it (the message's links and phone numbers);
        the phrase lookup starts as soon as triage has picked the phrase.
        """
        profile = self.store.get_profile(parent_id) or Profile(id=parent_id, name=parent_id)
        trace = Trace()

        with _timed(trace, "normalize") as step:
            message = normalize(raw)
            step.detail = f"channel={message.channel} urls={len(message.urls)} phones={len(message.phones)}"

        key = self.cache_key(message, profile)
        if use_cache and (cached := self.store.verdict_cache_get(key, self.settings.verdict_cache_ttl_seconds)):
            yield {"type": "result", "result": self._serve_cached(cached, message, trace, profile, parent_id)}
            return

        yield {"type": "step", "step": "reading"}
        mode = self.budget.status()
        if mode.mode != NORMAL:
            trace.steps.append(TraceStep(name="budget", detail=f"{mode.mode}: {mode.reason}"))

        with _timed(trace, "redact") as step:
            redacted = redact(message.body)
            step.detail = f"redacted {redacted.total} item(s) {redacted.counts}" if redacted.total else "nothing to redact"

        with _timed(trace, "memory") as step:
            contact = compare_to_contacts(profile, message)
            step.detail = f"{contact.status}: {contact.reason}"

        if quick := quick_check(message, contact):
            trace.steps.append(TraceStep(name="quick_check", detail=f"{quick.label} preview: {quick.reason}"))
            yield {"type": "quick", "label": quick.label, "reason": quick.reason}

        lookups: list[Lookup] = []
        # Not a ``with`` block: a report page still loading when the lookups are done is left behind, not waited for.
        pool = ThreadPoolExecutor(max_workers=self.settings.max_search_calls + 2)
        try:
            triage_job = pool.submit(self._triage, message, redacted.text, trace)
            desk = EvidenceDesk(self.search, self.store, self.settings, trace, self.budget)
            candidates = candidates_for(message.urls, message.phones, message.sender, contact.status != "unknown")
            jobs: dict[tuple[str, str], Future[Lookup]] = {}
            early_phrase = quick.phrase if quick else None
            if contact.status == "unknown":
                # Links and numbers need no model to be looked up, nor does a known scam script's wording: start now.
                # Without that wording, keep a slot for triage's phrase.
                early = self._plan(message, candidates, early_phrase)
                self._start_lookups(pool, desk, early if early_phrase else early[: self.settings.max_search_calls - 1], jobs)
                if jobs:
                    yield {"type": "step", "step": "searching"}

            triage = triage_job.result()
            triage_ok = not any(s.name == "triage" and s.error for s in trace.steps)
            fast = self._fast_path_note(triage, contact, profile, message, quick)
            if fast:
                wait(jobs.values())  # nothing may still be writing to the trace once the result is built
                verdict = self._fast_verdict(triage, contact, fast)
                trace.steps.append(TraceStep(name="fast_path", detail=f"{fast}: skipped evidence and verdict model"))
            else:
                searching = bool(jobs)
                plan = self._plan(message, candidates, early_phrase or triage.key_phrase)
                self._start_lookups(pool, desk, plan, jobs)
                if jobs and not searching:
                    yield {"type": "step", "step": "searching"}
                lookups = self._collect(pool, jobs, message, trace)
                if lookups:
                    yield {"type": "step", "step": "found", "count": len({h.url for lk in lookups for h in lk.hits})}
                yield {"type": "step", "step": "deciding"}
                verdict = self._verdict(message, redacted.text, triage, contact, lookups, profile, trace, triage_ok)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

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
        yield {"type": "result", "result": result}

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

    def _fast_path_note(
        self, triage: Triage, contact: ContactMatch, profile: Profile, message: Message, quick: QuickAnswer | None = None
    ) -> str | None:
        sig = triage.signals
        if quick and quick.label == "red":
            return None  # a known scam script always gets the full check, whatever triage thought
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
            evidence_note=f"Not checked online ({why}).",
        )

    # --- evidence ---------------------------------------------------------------------------------
    def _plan(self, message: Message, candidates: dict, key_phrase: str | None) -> list[dict]:
        """The lookups for this message, most informative first: the first link, the first number, triage's phrase,
        then the rest, at most ``max_search_calls``. Deterministic: a model call to plan them cost ~1-2 s and
        picked the same lookups."""
        return validate_plan(
            {"lookups": default_plan(candidates, key_phrase)}, candidates, message.body, self.settings.max_search_calls
        )

    @staticmethod
    def _start_lookups(
        pool: ThreadPoolExecutor, desk: EvidenceDesk, plan: list[dict], jobs: dict[tuple[str, str], Future[Lookup]]
    ) -> None:
        for item in plan:
            if (item["skill"], item["value"]) not in jobs:
                jobs[(item["skill"], item["value"])] = pool.submit(SKILLS[item["skill"]], desk, item["value"])

    def _collect(
        self, pool: ThreadPoolExecutor, jobs: dict[tuple[str, str], Future[Lookup]], message: Message, trace: Trace
    ) -> list[Lookup]:
        """Wait for the lookups. Meanwhile spend the one allowed Tavily extract on the first report page a lookup
        returns, so the verdict reads more than a snippet; once the last lookup is in, the page gets only
        ``EXTRACT_GRACE_SECONDS`` more and is left out if it is still loading (it is cached for next time)."""
        page: tuple[str, Future[tuple[str, list[TraceStep]]]] | None = None
        for job in as_completed(jobs.values()):
            if page is None and self.settings.max_extract_calls > 0 and (url := _page_to_read(job.result(), message)):
                page = (url, pool.submit(self._read_page, url))
        lookups = [job.result() for job in jobs.values()]
        if page:
            url, reading = page
            try:
                text, steps = reading.result(timeout=EXTRACT_GRACE_SECONDS)
            except FutureTimeout:
                trace.steps.append(
                    TraceStep(name="tavily:extract", kind="tool", detail=f"not used: page still loading after the lookups | q={url}")
                )
            else:
                trace.steps.extend(steps)
                hit = next(h for lk in lookups for h in lk.hits if h.url == url)
                if len(text) > len(hit.content):
                    hit.excerpt = text
        return lookups

    def _read_page(self, url: str) -> tuple[str, list[TraceStep]]:
        """One Tavily extract, traced on its own so a page that arrives too late never touches the message's trace."""
        steps = Trace()
        text = EvidenceDesk(self.search, self.store, self.settings, steps, self.budget).extract(url)
        return text, steps.steps

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
            family_points = _parse_points(data.get("family_points"))
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            _mark_error(trace, "verdict", exc)
            degraded = True
            score = 15 if contact.status == "trusted" else 50
            flags = []
            safe_action = "We could not fully check this message. Don't reply, click or pay; ask your family first."
            family_points = []

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
            family_points=(family_points or default_family_points(triage, flags, degraded)) if label != "green" else [],
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


def _parse_points(raw: object) -> list[str]:
    """The verdict model's family notes: non-empty strings, at most ``MAX_FAMILY_POINTS``. A bare string (a model
    that still wrote one paragraph) is kept as a single note rather than dropped."""
    items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    points = [" ".join(str(p).split()) for p in items if isinstance(p, (str, int, float))]
    return [p for p in points if p][:MAX_FAMILY_POINTS]


def default_family_points(triage: Triage, flags: list[RedFlag], degraded: bool = False) -> list[str]:
    """Notes for the family when the model gave none (or failed): what it is, why, and what to tell her."""
    what = triage.summary.strip() or "A message from a sender she doesn't know."
    if degraded:
        why = "ScamShield could not fully check it, so treat it as suspicious."
    elif flags:
        why = flags[0].text
    else:
        why = "It has the marks of a scam."
    return [
        what,
        why,
        "Tell her not to reply, click, pay or call back.",
        "If it names someone she knows, call that person on the number she already has.",
    ]


def _page_to_read(lookup: Lookup, message: Message) -> str | None:
    """The first report page of a lookup worth one extract: never a page on a domain the message itself links to."""
    message_hosts = {domain_of(u) for u in message.urls}
    for hit in lookup.hits:
        host = domain_of(hit.url)
        if host in message_hosts or any(host == h or host.endswith("." + h) for h in NO_EXTRACT_HOSTS):
            continue
        return hit.url
    return None


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
