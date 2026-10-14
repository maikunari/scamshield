"""The family side: the passphrase gate, the dashboard's read models, profile validation and the demo family.

* **Gate.** One passphrase per family (parent id), no accounts. ``POST /api/family/login`` trades it for a signed
  session, set as an HttpOnly cookie for the dashboard and returned as a bearer token for scripts. Sessions are
  HMAC-signed (``SESSION_SECRET``, or a secret generated once and kept in the database) and carry a fingerprint
  of the passphrase hash, so changing the passphrase signs everyone out. The parent ``/check`` page and
  ``/api/check`` stay public.
* **Dashboard read models.** ``check_summary`` is one row of the "recent checks" list, joined to its alert.
* **Memory edits.** ``clean_profile`` validates the profile the dashboard saves.
* **Demo.** ``load_demo`` resets the demo parent to the seed profile and a few past checks (one red with an alert)
  so a judge never types.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from .config import Settings
from .memory import Contact, KnownService, MemoryStore, Profile
from .models import AnalysisResult
from .normalize import to_e164

SESSION_VERSION = "v1"
SNIPPET_CHARS = 160


# --- gate ---------------------------------------------------------------------------------------------


def _scrypt(passphrase: str, salt: bytes) -> str:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _unb64(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()


class FamilyAuth:
    """Passphrase check and session tokens. Stateless apart from the database it reads."""

    def __init__(self, settings: Settings, store: MemoryStore):
        self.settings = settings
        self.store = store

    # passphrases
    def set_passphrase(self, parent_id: str, passphrase: str) -> None:
        salt = secrets.token_bytes(16)
        self.store.auth_set(parent_id, salt.hex(), _scrypt(passphrase, salt))

    def verify(self, parent_id: str, passphrase: str) -> bool:
        stored = self.store.auth_get(parent_id)
        if stored is None:
            return False
        salt, digest = stored
        return hmac.compare_digest(_scrypt(passphrase, bytes.fromhex(salt)), digest)

    def ensure_configured_passphrase(self, parent_id: str) -> None:
        """The demo parent's passphrase is whatever ``FAMILY_PASSPHRASE`` says now (the public demo one by default)."""
        if not self.verify(parent_id, self.settings.family_passphrase):
            self.set_passphrase(parent_id, self.settings.family_passphrase)

    # sessions
    def _secret(self) -> bytes:
        configured = self.settings.session_secret
        return (configured or self.store.kv_get_or_set("session_secret", lambda: secrets.token_hex(32))).encode()

    def _fingerprint(self, parent_id: str) -> str | None:
        stored = self.store.auth_get(parent_id)
        return stored[1][:12] if stored else None

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret(), payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, parent_id: str) -> str:
        fingerprint = self._fingerprint(parent_id)
        if fingerprint is None:
            raise ValueError(f"no passphrase set for {parent_id}")
        payload = f"{SESSION_VERSION}.{_b64(parent_id)}.{int(time.time()) + self.settings.session_ttl_seconds}.{fingerprint}"
        return f"{payload}.{self._sign(payload)}"

    def login(self, parent_id: str, passphrase: str) -> str | None:
        return self.issue(parent_id) if passphrase and self.verify(parent_id, passphrase) else None

    def parent_for(self, token: str | None) -> str | None:
        """The parent id a valid, unexpired session belongs to; None for anything else."""
        parts = (token or "").split(".")
        if len(parts) != 5 or parts[0] != SESSION_VERSION:
            return None
        payload = ".".join(parts[:4])
        if not hmac.compare_digest(self._sign(payload), parts[4]):
            return None
        try:
            parent_id, expires = _unb64(parts[1]), int(parts[2])
        except (ValueError, UnicodeDecodeError):
            return None
        if expires < time.time() or self._fingerprint(parent_id) != parts[3]:
            return None
        return parent_id


# --- dashboard read models ----------------------------------------------------------------------------


def trace_totals(result: AnalysisResult) -> dict:
    steps = result.trace.steps
    models = [s.model for s in steps if s.kind == "model" and s.model]
    return {
        "model_calls": sum(1 for s in steps if s.kind == "model" and s.model and not s.error),
        "models": list(dict.fromkeys(models)),
        "tokens": result.trace.total_tokens,
        "latency_ms": round(result.trace.total_latency_ms, 1),
        "cost_usd": round(result.trace.total_cost_usd, 6),
    }


def check_summary(result: AnalysisResult, alert: dict | None) -> dict:
    """One row of the recent-checks list. The full result (with the trace and the call script) is
    ``GET /results/{id}``; the body is empty for a 🟢 check the family did not opt in to keep."""
    body = " ".join(result.message.body.split())
    snippet = body if len(body) <= SNIPPET_CHARS else body[: SNIPPET_CHARS - 1].rstrip() + "…"
    return {
        "id": result.id,
        "created_at": result.created_at,
        "label": result.verdict.label,
        "score": result.verdict.score,
        "channel": result.message.channel,
        "sender": result.message.sender,
        "subject": result.message.subject,
        "snippet": snippet,
        "body_kept": bool(body),
        "scam_type": result.triage.scam_type,
        "summary": result.triage.summary,
        "contact": {"status": result.contact.status, "name": result.contact.name},
        "offline": result.offline,
        "demo": result.demo,
        "degraded": result.verdict.degraded,
        "trace": trace_totals(result),
        "alert": None
        if alert is None
        else {
            "id": alert["id"],
            "created_at": alert["created_at"],
            "acked_at": alert["acked_at"],
            "acked_by": alert["acked_by"],
            "acked_via": alert["acked_via"],
            "deliveries": [{k: d[k] for k in ("contact", "channel", "status", "target", "detail", "at")} for d in alert["deliveries"]],
        },
    }


def list_checks(store: MemoryStore, parent_id: str, limit: int = 30) -> list[dict]:
    alerts = {a["id"]: a for a in store.alerts_for_parent(parent_id)}
    by_body = {h: alerts.get(i) for h, i in store.alert_ids_by_body(parent_id).items()}
    return [check_summary(result, by_body.get(body_hash)) for body_hash, result in store.recent_analyses(parent_id, limit)]


# --- memory edits -------------------------------------------------------------------------------------

MAX_ENTRIES = 30
MAX_VALUES = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,}$")


class ProfileError(ValueError):
    """The profile the family tried to save is not valid; the message is shown to them."""


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _phones(values: list[str], who: str) -> list[str]:
    out = []
    for raw in (v.strip() for v in values if v.strip()):
        if to_e164(raw) is None:
            raise ProfileError(f"{who}: “{raw}” does not look like a phone number")
        out.append(raw)
    return _dedupe(out)


def _emails(values: list[str], who: str) -> list[str]:
    out = []
    for raw in (v.strip().lower() for v in values if v.strip()):
        if not EMAIL_RE.match(raw):
            raise ProfileError(f"{who}: “{raw}” does not look like an email address")
        out.append(raw)
    return _dedupe(out)


def _domains(values: list[str], who: str) -> list[str]:
    out = []
    for raw in (v.strip().lower() for v in values if v.strip()):
        host = re.sub(r"^[a-z]+://", "", raw).split("/")[0].removeprefix("www.")
        if not DOMAIN_RE.match(host):
            raise ProfileError(f"{who}: “{raw}” does not look like a website address (like lakeviewbank.example.com)")
        out.append(host)
    return _dedupe(out)


def _senders(values: list[str]) -> list[str]:
    return _dedupe([v.strip() for v in values if v.strip()])


def _check_count(label: str, values: list) -> None:
    if len(values) > MAX_VALUES:
        raise ProfileError(f"{label}: at most {MAX_VALUES} entries")


def clean_profile(profile: Profile, parent_id: str) -> Profile:
    """Trim, normalise and validate a profile saved from the dashboard. Names identify contacts (alert limits and
    Telegram links are keyed by name), so they must be filled in and unique."""
    name = profile.name.strip()
    if not name:
        raise ProfileError("Her name can't be empty")
    if len(profile.trusted_contacts) > MAX_ENTRIES or len(profile.known_services) > MAX_ENTRIES:
        raise ProfileError(f"At most {MAX_ENTRIES} contacts and {MAX_ENTRIES} services")

    contacts: list[Contact] = []
    for c in profile.trusted_contacts:
        cname = c.name.strip()
        if not cname:
            raise ProfileError("Every trusted contact needs a name")
        _check_count(cname, c.phones)
        _check_count(cname, c.emails)
        contacts.append(
            Contact(
                name=cname,
                relationship=c.relationship.strip().lower(),
                phones=_phones(c.phones, cname),
                emails=_emails(c.emails, cname),
            )
        )
    services: list[KnownService] = []
    for s in profile.known_services:
        sname = s.name.strip()
        if not sname:
            raise ProfileError("Every known service needs a name")
        for values in (s.domains, s.phones, s.senders):
            _check_count(sname, values)
        services.append(
            KnownService(
                name=sname,
                kind=s.kind.strip().lower(),
                domains=_domains(s.domains, sname),
                phones=_phones(s.phones, sname),
                senders=_senders(s.senders),
            )
        )
    for label, rows in (("contact", contacts), ("service", services)):
        names = [r.name.lower() for r in rows]
        if len(set(names)) != len(names):
            raise ProfileError(f"Two {label}s have the same name; names must be different")
    languages = _dedupe([x.strip().lower() for x in profile.languages if x.strip()]) or ["en"]
    return Profile(
        id=parent_id,
        name=name,
        languages=languages,
        trusted_contacts=contacts,
        known_services=services,
        store_green_bodies=profile.store_green_bodies,
    )


# --- demo family --------------------------------------------------------------------------------------


def _ago(now: datetime, hours: float) -> str:
    return (now - timedelta(hours=hours)).isoformat(timespec="seconds")


def load_demo(store: MemoryStore, auth: FamilyAuth, settings: Settings, seed_profile: Profile) -> dict:
    """Reset the demo parent: seed profile, the configured passphrase, and the sample past checks from
    ``seed/demo_checks.json`` with their alerts. The history is stored as it would have been left by real
    traffic (a 🟢 body is dropped), is marked ``demo`` and is dated relative to now. Nothing is sent."""
    parent_id = settings.demo_parent_id
    profile = seed_profile.model_copy(update={"id": parent_id})
    store.clear_family_activity(parent_id)
    store.save_profile(profile)
    auth.ensure_configured_passphrase(parent_id)  # unchanged when it already matches, so open dashboards stay signed in

    now = datetime.now(timezone.utc)
    spec = json.loads((settings.seed_dir / "demo_checks.json").read_text(encoding="utf-8"))
    alerts = 0
    for row in spec["checks"]:
        result = AnalysisResult.model_validate(
            {**row["result"], "id": f"demo-{row['key']}", "parent_id": parent_id, "created_at": _ago(now, row["hours_ago"]), "demo": True}
        )
        store.save_analysis(result, keep_body=profile.store_green_bodies)
        if not (alert := row.get("alert")):
            continue
        body_hash = hashlib.sha256(result.message.body.encode()).hexdigest()
        alert_id = f"demo-{row['key']}"
        store.alert_create(alert_id, parent_id, result.id, body_hash, secrets.token_urlsafe(16), at=result.created_at)
        for d in alert["deliveries"]:
            store.alert_delivery_add(
                alert_id, d["contact"], d["channel"], d["status"],
                target=d.get("target", ""), detail=d.get("detail", ""), at=result.created_at,
            )
        if "acked_hours_ago" in alert:
            store.alert_ack(alert_id, alert["acked_by"], alert["acked_via"], at=_ago(now, alert["acked_hours_ago"]))
        alerts += 1
    return {
        "parent_id": parent_id,
        "name": profile.name,
        "checks": len(spec["checks"]),
        "alerts": alerts,
        "passphrase": settings.family_passphrase if settings.demo_passphrase_is_public else None,
    }
