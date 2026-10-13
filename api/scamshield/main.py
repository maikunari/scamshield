"""FastAPI core API.

Routes:

* ``POST /api/check`` - the parent ``/check`` page. Takes a pasted or shared message, runs the
  pipeline for the demo parent and returns only what the parent screen shows (no family script).
* ``PUT /parents/{id}/profile``, ``POST /parents/{id}/analyze``, ``GET /results/{id}``, ``GET /budget`` -
  full pipeline access for the family side and tests.
* ``/`` - the Next.js static export in ``web/out`` (``SCAMSHIELD_WEB_DIR``) when it exists, so one
  container serves both the PWA and the API. Alerts and the dashboard come later.

Both analyze routes share the per-IP rate limit.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .memory import Profile
from .models import AnalysisResult, Channel, ContactMatch, Label, RawMessage, RedFlag
from .pipeline import Pipeline
from .ratelimit import RateLimiter, client_ip
from .runtime import build_pipeline, load_profile

MAX_CHECK_CHARS = 8000

app = FastAPI(title="ScamShield core API", version="0.1.0")


@lru_cache
def get_pipeline() -> Pipeline:
    """One pipeline per process. Tests clear this cache (and ``get_limiter``) after changing the environment."""
    settings = Settings.from_env()
    pipeline = build_pipeline(settings)
    ensure_demo_profile(pipeline, settings)
    return pipeline


@lru_cache
def get_limiter() -> RateLimiter:
    s = get_pipeline().settings
    return RateLimiter(s.rate_limit_per_window, s.rate_limit_window_seconds)


def ensure_demo_profile(pipeline: Pipeline, settings: Settings) -> None:
    """Load the seed profile for the demo parent once, so ``/api/check`` has memory from the first request."""
    if pipeline.store.get_profile(settings.demo_parent_id) is not None:
        return
    seed_profile = settings.seed_dir / "profile.json"
    if not seed_profile.exists():
        return
    profile = load_profile(settings.seed_dir)
    if profile.id == settings.demo_parent_id:
        pipeline.store.save_profile(profile)


def enforce_rate_limit(request: Request, pipeline: Pipeline) -> None:
    ip = client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        trust_forwarded=pipeline.settings.trust_forwarded_for,
    )
    wait = get_limiter().check(ip)
    if wait:
        raise HTTPException(429, "Too many requests, please wait a moment.", headers={"Retry-After": str(int(wait) + 1)})


# --- parent /check -----------------------------------------------------------------------------------


class CheckRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_CHECK_CHARS)
    sender: str | None = Field(default=None, max_length=200)
    channel: Channel = "paste"
    subject: str | None = Field(default=None, max_length=500)


class FamilyNotice(BaseModel):
    name: str | None = None  # first name shown on the red screen ("Kenji has been told")
    notified: bool = False  # flips when the family alert (P0 #6) is wired


class CheckResponse(BaseModel):
    """What the parent screen needs, and nothing the parent should not see (no family script, no trace)."""

    id: str
    label: Label
    score: int
    red_flags: list[RedFlag]
    safe_action: str
    evidence_note: str
    contact: ContactMatch  # trusted / known_service / unknown: lets the green screen say *why*
    family: FamilyNotice
    language: str = "en"
    offline: bool = False
    degraded: bool = False
    budget_mode: str = "normal"


def family_first_name(profile: Profile | None) -> str | None:
    if profile is None:
        return None
    preferred = ("son", "daughter", "child")
    contacts = sorted(profile.trusted_contacts, key=lambda c: (c.relationship.lower() not in preferred))
    for c in contacts:
        if c.name.strip():
            return c.name.split()[0]
    return None


def to_check_response(result: AnalysisResult, profile: Profile | None) -> CheckResponse:
    v = result.verdict
    return CheckResponse(
        id=result.id,
        label=v.label,
        score=v.score,
        red_flags=v.red_flags,
        safe_action=v.safe_action,
        evidence_note=v.evidence_note,
        contact=result.contact,
        family=FamilyNotice(name=family_first_name(profile), notified=False),
        language=result.triage.language,
        offline=result.offline,
        degraded=v.degraded,
        budget_mode=result.budget_mode,
    )


@app.post("/api/check")
def check(req: CheckRequest, request: Request) -> CheckResponse:
    if not req.body.strip():
        raise HTTPException(422, "message is empty")
    pipeline = get_pipeline()
    enforce_rate_limit(request, pipeline)
    parent_id = pipeline.settings.demo_parent_id
    raw = RawMessage(body=req.body, sender=req.sender or None, channel=req.channel, subject=req.subject)
    result = pipeline.analyze(raw, parent_id)
    return to_check_response(result, pipeline.store.get_profile(parent_id))


# --- health, budget + full pipeline ------------------------------------------------------------------


@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    pipeline = get_pipeline()
    return {
        "ok": True,
        "llm": pipeline.llm.name,
        "search": pipeline.search.name,
        "offline": pipeline.llm.offline or pipeline.search.offline,
        "budget_mode": pipeline.budget.mode,
    }


@app.get("/budget")
def budget() -> dict:
    return get_pipeline().budget.status().as_dict()


@app.put("/parents/{parent_id}/profile")
def put_profile(parent_id: str, profile: Profile) -> Profile:
    profile.id = parent_id
    get_pipeline().store.save_profile(profile)
    return profile


@app.post("/parents/{parent_id}/analyze")
def analyze(parent_id: str, raw: RawMessage, request: Request) -> AnalysisResult:
    pipeline = get_pipeline()
    enforce_rate_limit(request, pipeline)
    return pipeline.analyze(raw, parent_id)


@app.get("/results/{analysis_id}")
def get_result(analysis_id: str) -> AnalysisResult:
    result = get_pipeline().store.get_analysis(analysis_id)
    if result is None:
        raise HTTPException(404, "no such analysis")
    return result


# --- static web (Next.js export) ---------------------------------------------------------------------


def mount_web(application: FastAPI, web_dir: Path) -> bool:
    """Serve the static export at ``/`` (after the API routes, so ``/api/*`` still wins)."""
    if not (web_dir / "index.html").exists():
        return False
    application.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return True


mount_web(app, Settings.from_env().web_dir)
