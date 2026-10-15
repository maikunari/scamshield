"""Live smoke test: one seed message end to end against the real Token Factory and Tavily, plus a ``json_schema``
check on each of the three Nemotron models. Needs NEBIUS_API_KEY and TAVILY_API_KEY; the transports are injectable so
the logic is tested offline with mocked HTTP."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import TIER_ENV, Settings, resolve_model_id
from .discovery import fetch_models
from .llm.schemas import TRIAGE_SCHEMA
from .llm.tokenfactory import TokenFactoryClient
from .memory import MemoryStore
from .models import AnalysisResult
from .pipeline import Pipeline
from .prompts import TRIAGE_SYSTEM, triage_user
from .runtime import load_profile, load_seed_messages
from .search.tavily import TavilyClient

PROBE_TIERS = ("fast", "mid", "reason")  # Nano, Super, Ultra
SEED_ID = "m02"  # the delivery-fee SMS: a phone-less link scam that exercises triage, search and verdict


class SmokeUnavailable(RuntimeError):
    """The environment cannot run the smoke test (missing keys, or offline forced)."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def run_smoke(
    settings: Settings,
    *,
    seed_id: str = SEED_ID,
    llm_transport: httpx.BaseTransport | None = None,
    search_transport: httpx.BaseTransport | None = None,
) -> tuple[list[Check], AnalysisResult | None]:
    missing = [k for k, v in (("NEBIUS_API_KEY", settings.nebius_api_key), ("TAVILY_API_KEY", settings.tavily_api_key)) if not v]
    if missing:
        raise SmokeUnavailable(f"{' and '.join(missing)} not set: the smoke test only runs against the real services")
    if settings.force_offline:
        raise SmokeUnavailable("SCAMSHIELD_OFFLINE is set: unset it to run against the real services")

    checks: list[Check] = []
    llm = TokenFactoryClient(settings.nebius_api_key or "", settings.nebius_base_url, transport=llm_transport, backoff=0.5)
    search = TavilyClient(settings.tavily_api_key or "", transport=search_transport)

    # 1. Are the configured models really served? (case-insensitive: the catalog mixes casing)
    listed: list[str] = []
    try:
        listed = [m["id"] for m in fetch_models(settings.nebius_base_url, settings.nebius_api_key or "", transport=llm_transport)]
        checks.append(Check("GET /models", True, f"{len(listed)} models"))
    except httpx.HTTPError as exc:
        checks.append(Check("GET /models", False, str(exc)))
    resolved: dict[str, str] = {}
    for tier in PROBE_TIERS:
        wanted = settings.models[tier]
        found = resolve_model_id(wanted, listed) if listed else None
        if found:
            resolved[tier] = found
        checks.append(Check(f"{TIER_ENV[tier]} listed", found is not None, found or f"{wanted} not in /models"))

    # 2. Does each model honour response_format=json_schema (no fallback)?
    probe_user = triage_user("Your package could not be delivered. Pay $1.99: http://parcel.example.com/pay", "212-555-0187", "sms", ["http://parcel.example.com/pay"], [])
    for tier in PROBE_TIERS:
        model = resolved.get(tier) or settings.models[tier]
        res = llm.probe_json_schema(model, TRIAGE_SCHEMA, TRIAGE_SYSTEM, probe_user)
        checks.append(Check(f"json_schema on {model}", res.ok, res.detail if not res.ok else f"{res.tokens_in}+{res.tokens_out} tok, {res.latency_ms:.0f} ms"))

    # 3. One seed message through the whole pipeline on a throwaway database.
    raw = dict(load_seed_messages(settings.seed_dir)).get(seed_id)
    if raw is None:
        checks.append(Check(f"seed message {seed_id}", False, "not found"))
        return checks, None
    store = MemoryStore(":memory:")
    profile = load_profile(settings.seed_dir)
    store.save_profile(profile)
    result = Pipeline(settings, llm, search, store).analyze(raw, profile.id)
    steps = result.trace.steps
    model_steps = {s.name: s for s in steps if s.kind == "model"}
    checks.append(Check("pipeline not offline", not result.offline, "live Token Factory + Tavily" if not result.offline else "fakes in use"))
    checks.append(Check("no step errors", not any(s.error for s in steps), "; ".join(f"{s.name}: {s.error}" for s in steps if s.error)))
    checks.append(Check("triage and verdict ran", set(model_steps) >= {"triage", "verdict"}, ", ".join(model_steps)))
    checks.append(Check("tokens and cost recorded", all(s.tokens_in > 0 and s.cost_usd > 0 for s in model_steps.values()) and bool(model_steps), f"${result.trace.total_cost_usd:.5f}, {result.trace.total_tokens} tokens"))
    tiers = [s.tier for s in model_steps.values()]
    checks.append(Check("routed Nano -> Ultra", tiers == ["fast", "reason"], " -> ".join(f"{s.tier}:{s.model}" for s in model_steps.values())))
    tavily = [s for s in steps if s.name.startswith("tavily:") and "cache hit" not in s.detail]
    checks.append(Check("Tavily searched", bool(tavily) and not any(s.error for s in tavily), f"{len(tavily)} live call(s)"))
    checks.append(Check(f"{seed_id} scored as a scam", result.verdict.label == "red", f"{result.verdict.label} ({result.verdict.score})"))
    return checks, result


def format_checks(checks: list[Check]) -> str:
    return "\n".join(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f"  - {c.detail}" if c.detail else "") for c in checks)
