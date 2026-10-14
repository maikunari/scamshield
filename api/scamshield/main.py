"""FastAPI core API.

Routes:

* ``POST /api/check`` - the parent ``/check`` page. Takes a pasted or shared message, runs the
  pipeline for the demo parent and returns only what the parent screen shows (no family script).
* ``PUT /parents/{id}/profile``, ``POST /parents/{id}/analyze``, ``GET /results/{id}``, ``GET /budget`` -
  full pipeline access for the family side and tests.
* ``/`` - the Next.js static export in ``web/out`` (``SCAMSHIELD_WEB_DIR``) when it exists, so one
  container serves both the PWA and the API, including the family dashboard at ``/family/``.
* The family gate (family.py): ``POST /api/family/login`` trades the family passphrase for a session cookie (and a
  bearer token). Every ``/parents/{id}/...`` route and ``/results/{id}`` needs that session; ``/api/check``, the
  parent ``/check`` page, the alert "I called her" links and the Telegram webhook (own secret) stay public.
  ``POST /api/demo/load`` resets the demo family (profile, passphrase, sample past checks).
* Family alerts (alerts.py): a 🔴 result from either analyze route alerts the parent's family contact(s).
  ``POST /api/telegram/webhook`` takes the bot's updates (``/start <code>`` links a chat, "I called her" acknowledges),
  ``POST /parents/{id}/telegram-link`` makes the link a family member opens, ``GET /api/alerts/{id}/ack`` is the
  email's "I called her" link and ``GET /parents/{id}/alerts`` lists alerts, their deliveries and acknowledgements.
* Privacy (privacy.py): ``GET /parents/{id}/export``, ``DELETE /parents/{id}/data?confirm=yes`` and
  ``DELETE /parents/{id}/checks/{check_id}``, plus the parent app's ``/api/my-data[/export]`` and ``/api/check/{id}``.

Both analyze routes share the per-IP rate limit.
"""

from __future__ import annotations

import html
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from weakref import WeakKeyDictionary

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .alerts import AlertOutcome, AlertService, alert_view, family_contacts
from .config import Settings
from .family import FamilyAuth, ProfileError, clean_profile, list_checks, load_demo
from .memory import Profile
from .models import AnalysisResult, Channel, ContactMatch, Label, RawMessage, RedFlag
from .pipeline import Pipeline
from .privacy import Retention, export_parent
from .ratelimit import RateLimiter, client_ip
from .runtime import build_pipeline, load_profile

MAX_CHECK_CHARS = 8000



@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Register the Telegram webhook when a token, a webhook secret and an https PUBLIC_BASE_URL are all set."""
    get_alerts().register_webhook()
    get_retention().sweep()
    yield


app = FastAPI(title="ScamShield core API", version="0.1.0", lifespan=lifespan)


@lru_cache
def get_pipeline() -> Pipeline:
    """One pipeline per process. Tests clear this cache (and ``get_limiter``) after changing the environment."""
    settings = Settings.from_env()
    pipeline = build_pipeline(settings)
    ensure_demo_profile(pipeline, settings)
    if pipeline.store.get_profile(settings.demo_parent_id) is not None:
        FamilyAuth(settings, pipeline.store).ensure_configured_passphrase(settings.demo_parent_id)
    return pipeline


@lru_cache
def get_alerts() -> AlertService:
    pipeline = get_pipeline()
    return AlertService(pipeline.settings, pipeline.store)


@lru_cache
def get_retention() -> Retention:
    pipeline = get_pipeline()
    return Retention(pipeline.settings, pipeline.store)


@lru_cache
def get_limiter() -> RateLimiter:
    s = get_pipeline().settings
    return RateLimiter(s.rate_limit_per_window, s.rate_limit_window_seconds)


def get_auth() -> FamilyAuth:
    pipeline = get_pipeline()
    return FamilyAuth(pipeline.settings, pipeline.store)


_login_limiters: WeakKeyDictionary[Pipeline, RateLimiter] = WeakKeyDictionary()


def get_login_limiter() -> RateLimiter:
    """Guesses at the family passphrase (and demo resets) per client IP. One per pipeline, so it follows the
    ``get_pipeline`` cache the way ``get_limiter`` does."""
    pipeline = get_pipeline()
    if pipeline not in _login_limiters:
        _login_limiters[pipeline] = RateLimiter(pipeline.settings.login_attempts_per_minute, 60)
    return _login_limiters[pipeline]


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


def enforce_rate_limit(request: Request, pipeline: Pipeline, limiter: RateLimiter | None = None) -> None:
    ip = client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        trust_forwarded=pipeline.settings.trust_forwarded_for,
    )
    wait = (limiter or get_limiter()).check(ip)
    if wait:
        raise HTTPException(429, "Too many requests, please wait a moment.", headers={"Retry-After": str(int(wait) + 1)})
    get_retention().maybe_sweep()


# --- family gate -------------------------------------------------------------------------------------

SESSION_COOKIE = "ss_family"


def session_parent(request: Request) -> str | None:
    """The parent id of the request's session: ``Authorization: Bearer <token from login>``, else the cookie."""
    auth = get_auth()
    header = request.headers.get("authorization", "")
    bearer = header[7:].strip() if header[:7].lower() == "bearer " else None
    return auth.parent_for(bearer) or auth.parent_for(request.cookies.get(SESSION_COOKIE))


def unauthorized() -> HTTPException:
    return HTTPException(401, "The family passphrase is needed.", headers={"WWW-Authenticate": "Bearer"})


def require_family(parent_id: str, request: Request) -> None:
    """Dependency for ``/parents/{parent_id}/...``: the session must belong to that family."""
    if session_parent(request) != parent_id:
        raise unauthorized()


class LoginRequest(BaseModel):
    passphrase: str = Field(max_length=200)
    parent_id: str | None = Field(default=None, max_length=100)


@app.post("/api/family/login")
def family_login(req: LoginRequest, request: Request, response: Response) -> dict:
    pipeline = get_pipeline()
    enforce_rate_limit(request, pipeline, get_login_limiter())
    parent_id = req.parent_id or pipeline.settings.demo_parent_id
    token = get_auth().login(parent_id, req.passphrase)
    if token is None:
        raise HTTPException(401, "That passphrase didn't match.")
    profile = pipeline.store.get_profile(parent_id)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https" and pipeline.settings.trust_forwarded_for
    response.set_cookie(
        SESSION_COOKIE, token, max_age=pipeline.settings.session_ttl_seconds, httponly=True, samesite="lax", secure=bool(secure), path="/"
    )
    return {"parent_id": parent_id, "name": profile.name if profile else parent_id, "token": token}


@app.post("/api/family/logout")
def family_logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/family/session")
def family_session(request: Request) -> dict:
    parent_id = session_parent(request)
    if parent_id is None:
        raise unauthorized()
    profile = get_pipeline().store.get_profile(parent_id)
    return {"parent_id": parent_id, "name": profile.name if profile else parent_id}


@app.get("/api/family/gate")
def family_gate() -> dict:
    """What the login screen may say before anyone is signed in: whether the demo family can be loaded and, while
    the passphrase is the public demo one, what it is."""
    s = get_pipeline().settings
    return {
        "demo_load": s.demo_load_enabled,
        "demo_passphrase": s.family_passphrase if s.demo_load_enabled and s.demo_passphrase_is_public else None,
    }


@app.post("/api/demo/load")
def demo_load(request: Request) -> dict:
    """The judge path: reset the demo family to the seed profile plus a few past checks, so nobody types anything."""
    pipeline = get_pipeline()
    if not pipeline.settings.demo_load_enabled:
        raise HTTPException(404, "The demo family is switched off on this deployment.")
    enforce_rate_limit(request, pipeline, get_login_limiter())
    seed_profile = load_profile(pipeline.settings.seed_dir)
    return load_demo(pipeline.store, get_auth(), pipeline.settings, seed_profile)


# --- parent /check -----------------------------------------------------------------------------------


class CheckRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_CHECK_CHARS)
    sender: str | None = Field(default=None, max_length=200)
    channel: Channel = "paste"
    subject: str | None = Field(default=None, max_length=500)


class FamilyNotice(BaseModel):
    name: str | None = None  # first name shown on the red screen ("David has been told")
    notified: bool = False  # true once an alert really went out (a recorded, unsent alert stays false)


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


def to_check_response(result: AnalysisResult, profile: Profile | None, alert: AlertOutcome | None = None) -> CheckResponse:
    v = result.verdict
    return CheckResponse(
        id=result.id,
        label=v.label,
        score=v.score,
        red_flags=v.red_flags,
        safe_action=v.safe_action,
        evidence_note=v.evidence_note,
        contact=result.contact,
        family=FamilyNotice(name=family_first_name(profile), notified=bool(alert and alert.notified)),
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
    profile = pipeline.store.get_profile(parent_id)
    return to_check_response(result, profile, get_alerts().dispatch(result, profile))


# --- privacy: export and delete -----------------------------------------------------------------------
# The parent app calls the /api/my-data routes (always the demo parent, like /api/check); the family side
# calls the /parents/{id}/... ones. Deleting needs ``confirm=yes`` so a stray request cannot wipe a parent.


def require_confirm(confirm: str) -> None:
    if confirm != "yes":
        raise HTTPException(400, "Add confirm=yes to delete. This cannot be undone.")


def delete_all(parent_id: str) -> dict:
    deleted = get_pipeline().store.delete_parent_data(parent_id)
    return {"deleted": True, "parent_id": parent_id, "removed": deleted}


def delete_check(parent_id: str, check_id: str) -> dict:
    if not get_pipeline().store.delete_analysis(parent_id, check_id):
        raise HTTPException(404, "no such check")
    return {"deleted": True, "id": check_id}


@app.get("/parents/{parent_id}/export", dependencies=[Depends(require_family)])
def export_data(parent_id: str) -> dict:
    return export_parent(get_pipeline().store, parent_id)


@app.delete("/parents/{parent_id}/data", dependencies=[Depends(require_family)])
def delete_parent_data(parent_id: str, confirm: str = "") -> dict:
    require_confirm(confirm)
    return delete_all(parent_id)


@app.delete("/parents/{parent_id}/checks/{check_id}", dependencies=[Depends(require_family)])
def delete_parent_check(parent_id: str, check_id: str) -> dict:
    return delete_check(parent_id, check_id)


@app.get("/api/my-data/export")
def my_data_export() -> dict:
    return export_parent(get_pipeline().store, get_pipeline().settings.demo_parent_id)


@app.delete("/api/my-data")
def my_data_delete(confirm: str = "") -> dict:
    require_confirm(confirm)
    return delete_all(get_pipeline().settings.demo_parent_id)


@app.delete("/api/check/{check_id}")
def my_check_delete(check_id: str) -> dict:
    return delete_check(get_pipeline().settings.demo_parent_id, check_id)


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


@app.get("/parents/{parent_id}/profile", dependencies=[Depends(require_family)])
def get_profile(parent_id: str) -> Profile:
    profile = get_pipeline().store.get_profile(parent_id)
    if profile is None:
        raise HTTPException(404, "no such family")
    return profile


@app.put("/parents/{parent_id}/profile", dependencies=[Depends(require_family)])
def put_profile(parent_id: str, profile: Profile) -> Profile:
    """Save the family's edits to the parent's memory (trusted contacts, known services). Validated and
    normalised; the next check uses it."""
    try:
        cleaned = clean_profile(profile, parent_id)
    except ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc
    get_pipeline().store.save_profile(cleaned)
    return cleaned


@app.post("/parents/{parent_id}/analyze", dependencies=[Depends(require_family)])
def analyze(parent_id: str, raw: RawMessage, request: Request) -> AnalysisResult:
    pipeline = get_pipeline()
    enforce_rate_limit(request, pipeline)
    result = pipeline.analyze(raw, parent_id)
    get_alerts().dispatch(result, pipeline.store.get_profile(parent_id))
    return result


@app.get("/results/{analysis_id}")
def get_result(analysis_id: str, request: Request) -> AnalysisResult:
    """The full result with the trace and the family call script: family side only."""
    session = session_parent(request)
    if session is None:
        raise unauthorized()
    result = get_pipeline().store.get_analysis(analysis_id)
    if result is None or result.parent_id != session:
        raise HTTPException(404, "no such analysis")
    return result


# --- family alerts -----------------------------------------------------------------------------------


class TelegramLinkRequest(BaseModel):
    contact: str = Field(min_length=1, max_length=200)


@app.post("/parents/{parent_id}/telegram-link", dependencies=[Depends(require_family)])
def telegram_link(parent_id: str, req: TelegramLinkRequest) -> dict:
    """Show this to the family member: opening ``url`` (t.me/<bot>?start=<code>) links their Telegram chat."""
    profile = get_pipeline().store.get_profile(parent_id)
    link = get_alerts().create_telegram_link(profile, req.contact) if profile else None
    if link is None:
        raise HTTPException(404, "no such family contact")
    return link


@app.get("/parents/{parent_id}/alerts", dependencies=[Depends(require_family)])
def list_alerts(parent_id: str) -> list[dict]:
    return [alert_view(a) for a in get_pipeline().store.alerts_for_parent(parent_id)]


@app.post("/parents/{parent_id}/alerts/{alert_id}/ack", dependencies=[Depends(require_family)])
def ack_from_dashboard(parent_id: str, alert_id: str) -> dict:
    """"I called her" from the dashboard. Idempotent: ``newly`` says whether this call did it."""
    alert = get_pipeline().store.alert_get(alert_id)
    if alert is None or alert["parent_id"] != parent_id:
        raise HTTPException(404, "no such alert")
    newly = get_alerts().acknowledge(alert_id, "dashboard", "dashboard")
    return {"id": alert_id, "newly": bool(newly), **alert_view(get_pipeline().store.alert_get(alert_id) or {})}


# --- family dashboard ---------------------------------------------------------------------------------


@app.get("/parents/{parent_id}/checks", dependencies=[Depends(require_family)])
def recent_checks(parent_id: str, limit: int = 30) -> list[dict]:
    """Recent checks, newest first, each with its alert and the totals of its trace."""
    return list_checks(get_pipeline().store, parent_id, max(1, min(limit, 100)))


@app.get("/parents/{parent_id}/family", dependencies=[Depends(require_family)])
def family_info(parent_id: str) -> dict:
    """The family members who get alerts, which channels work for them, and whether Telegram is linked."""
    store = get_pipeline().store
    profile = store.get_profile(parent_id)
    if profile is None:
        raise HTTPException(404, "no such family")
    alerts = get_alerts()
    return {
        "parent": {"id": profile.id, "name": profile.name},
        "contacts": [
            {
                "name": c.name,
                "relationship": c.relationship,
                "emails": c.emails,
                "telegram_linked": store.telegram_chat(parent_id, c.name) is not None,
            }
            for c in family_contacts(profile)
        ],
        "telegram_configured": alerts.telegram is not None,
        "email_configured": alerts.email is not None,
    }


@app.post("/api/telegram/webhook")
def telegram_webhook(
    update: dict = Body(...),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    alerts = get_alerts()
    if alerts.telegram is None or not alerts.settings.telegram_webhook_secret:
        raise HTTPException(404, "telegram is not configured")
    if not alerts.webhook_secret_ok(x_telegram_bot_api_secret_token):
        raise HTTPException(403, "bad secret")
    alerts.handle_update(update)
    return {"ok": True}


def _ack_page(title: str, message: str, form_action: str | None = None, status: int = 200) -> HTMLResponse:
    form = (
        f'<form method="post" action="{html.escape(form_action, quote=True)}">'
        '<button style="font-size:20px;padding:14px 24px;border:0;border-radius:8px;background:#1b6e3c;color:#fff">'
        "Yes, I called her</button></form>"
        if form_action
        else ""
    )
    body = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        '<body style="font-family:Arial,sans-serif;max-width:520px;margin:48px auto;padding:0 16px;line-height:1.5">'
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{form}</body>"
    )
    return HTMLResponse(body, status_code=status)


@app.get("/api/alerts/{alert_id}/ack")
def ack_page(alert_id: str, t: str = "") -> HTMLResponse:
    """The email's "I called her" link. It only shows a confirm button: a mail scanner that prefetches links
    must not mark an unmade call as made."""
    if not get_alerts().token_valid(alert_id, t):
        return _ack_page("Link not valid", "This link is wrong or has expired.", status=404)
    return _ack_page("Did you call her?", "Confirm once you have spoken to her about this message.", f"/api/alerts/{alert_id}/ack?t={t}")


@app.post("/api/alerts/{alert_id}/ack")
def ack(alert_id: str, t: str = "") -> HTMLResponse:
    newly = get_alerts().acknowledge_with_token(alert_id, t)
    if newly is None:
        return _ack_page("Link not valid", "This link is wrong or has expired.", status=404)
    return _ack_page("Thanks", "Marked as called." if newly else "Already marked as called.")


# --- static web (Next.js export) ---------------------------------------------------------------------


def mount_web(application: FastAPI, web_dir: Path) -> bool:
    """Serve the static export at ``/`` (after the API routes, so ``/api/*`` still wins)."""
    if not (web_dir / "index.html").exists():
        return False
    application.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return True


mount_web(app, Settings.from_env().web_dir)
