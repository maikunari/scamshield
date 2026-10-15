"""Family alerts: when a check comes back 🔴, tell the parent's family contact(s) on Telegram and by email.

* One alert per message per parent (the message body hash), however often it is re-checked or re-sent.
* Each family contact gets at most ``alert_max_per_contact`` alerts per window; the rest are recorded as
  ``rate_limited`` instead of sent.
* A channel without credentials does not fail: the alert is recorded as it would have been sent
  (``status="recorded"``, with the payload), so the offline demo and the tests can read it back.
* "I called her" acknowledges the alert: a Telegram inline button (callback through the webhook) or an email link.
* 🟡 is not alerted: the spec batches it into a digest, which is a later slice.

Alert text is built from model output and scammer-controlled strings, so everything is HTML-escaped and only
http(s) evidence links become links.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import httpx

from .config import Settings
from .memory import Contact, MemoryStore, Profile
from .models import AnalysisResult
from .notify import DeliveryError, ResendClient, TelegramClient

log = logging.getLogger("scamshield.alerts")

FAMILY_RELATIONSHIPS = {"son", "daughter", "child"}  # the adult child; grandchildren and friends are not alerted
ALERT_LABELS = {"red"}
LINK_CODE_TTL = timedelta(days=7)
MAX_FLAGS = 3
MAX_POINTS = 4
DEFAULT_POINTS = [
    "She was sent a message that looks like a scam.",
    "Tell her not to reply, click, pay or call back.",
    "If it names someone she knows, call that person on the number she already has.",
]


def family_contacts(profile: Profile) -> list[Contact]:
    return [c for c in profile.trusted_contacts if c.relationship.strip().lower() in FAMILY_RELATIONSHIPS and c.name.strip()]


@dataclass
class AlertOutcome:
    alert_id: str | None = None
    created: bool = False
    notified: bool = False  # at least one real delivery went out (a recorded, unsent alert does not count)


@dataclass
class AlertContent:
    subject: str
    telegram: str
    email_html: str
    email_text: str
    ack_url: str | None
    check_url: str | None


def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    return url.strip() if parts.scheme in ("http", "https") and parts.netloc else None


def build_content(
    result: AnalysisResult, parent_name: str, alert_id: str, token: str, base_url: str | None
) -> AlertContent:
    """The alert in all three renderings. ``base_url`` (PUBLIC_BASE_URL) is where the links point; without it
    the link lines are left out rather than pointing nowhere."""
    first = parent_name.split()[0] if parent_name.strip() else "your parent"
    v = result.verdict
    flags = v.red_flags[:MAX_FLAGS]
    points = [p.strip() for p in v.family_points if p.strip()][:MAX_POINTS] or DEFAULT_POINTS
    sender = result.message.sender or "an unknown sender"
    check_url = f"{base_url}/family/?check={quote(result.id)}" if base_url else None
    ack_url = f"{base_url}/api/alerts/{quote(alert_id)}/ack?t={quote(token)}" if base_url else None
    subject = f"ScamShield: {first} got a message that looks like a scam"

    e = html.escape
    headline = f"{e(first)} may have been sent a scam (risk {v.score}/100)"

    # Telegram (HTML parse mode: <b>, <i>, <a href>)
    tg = [f"🔴 <b>{headline}</b>", f"From: {e(sender)} ({e(result.message.channel)})"]
    tg += ["", "<b>Before you call her</b>", *(f"• {e(p)}" for p in points)]
    if flags:
        tg.append("")
        tg.append("<b>Why it looks like a scam</b>")
        for i, f in enumerate(flags, 1):
            link = _safe_url(f.evidence_url)
            tg.append(f"{i}. {e(f.text)}" + (f' (<a href="{e(link, quote=True)}">evidence</a>)' if link else ""))
    if check_url:
        tg += ["", f'<a href="{e(check_url, quote=True)}">See the full check</a>']

    # Email, plain text
    tx = [headline, f"From: {sender} ({result.message.channel})", "", "Before you call her:"]
    tx += [f"- {p}" for p in points]
    tx.append("")
    if flags:
        tx.append("Why it looks like a scam:")
        for i, f in enumerate(flags, 1):
            link = _safe_url(f.evidence_url)
            tx.append(f"{i}. {f.text}" + (f" - evidence: {link}" if link else ""))
        tx.append("")
    if check_url:
        tx.append(f"See the full check: {check_url}")
    if ack_url:
        tx.append(f"Once you have called {first}, tap here so nobody else worries: {ack_url}")
    tx += ["", "Sent by ScamShield."]

    # Email, HTML
    notes = [f'<li style="margin:0 0 6px">{e(p)}</li>' for p in points]
    li = []
    for f in flags:
        link = _safe_url(f.evidence_url)
        extra = f' <a href="{e(link, quote=True)}" style="color:#0b5cad">evidence</a>' if link else ""
        li.append(f"<li style=\"margin:0 0 8px\">{e(f.text)}{extra}</li>")
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#1b1b1b;max-width:560px">',
        f'<h2 style="margin:0 0 8px;color:#a31515">🔴 {headline}</h2>',
        f'<p style="margin:0 0 16px;color:#555">From: {e(sender)} ({e(result.message.channel)})</p>',
        '<h3 style="margin:16px 0 8px">Before you call her</h3>'
        f'<ul style="padding-left:20px;margin:0">{"".join(notes)}</ul>',
    ]
    if li:
        parts.append(f'<h3 style="margin:16px 0 8px">Why it looks like a scam</h3><ol style="padding-left:20px">{"".join(li)}</ol>')
    if ack_url:
        parts.append(
            f'<p style="margin:24px 0"><a href="{e(ack_url, quote=True)}" style="background:#1b6e3c;color:#fff;'
            f'padding:12px 20px;border-radius:6px;text-decoration:none;display:inline-block">I called her</a></p>'
        )
    if check_url:
        parts.append(f'<p><a href="{e(check_url, quote=True)}" style="color:#0b5cad">See the full check</a></p>')
    parts.append('<p style="color:#777;font-size:13px">Sent by ScamShield.</p></div>')
    return AlertContent(subject, "\n".join(tg), "".join(parts), "\n".join(tx), ack_url, check_url)


class AlertService:
    def __init__(self, settings: Settings, store: MemoryStore, http: httpx.Client | None = None):
        self.settings = settings
        self.store = store
        self._http = http or httpx.Client()
        self.telegram = TelegramClient(settings.telegram_bot_token, self._http) if settings.telegram_configured else None
        self.email = (
            ResendClient(settings.resend_api_key, settings.alert_email_from, self._http)
            if settings.email_configured
            else None
        )
        self._bot_username: str | None = None

    # --- sending --------------------------------------------------------------------------------
    def dispatch(self, result: AnalysisResult, profile: Profile | None) -> AlertOutcome:
        """Alert the family about a 🔴 result. Safe to call for any result: other labels, a parent without a
        family contact and a message already alerted all return without sending."""
        if result.verdict.label not in ALERT_LABELS or profile is None:
            return AlertOutcome()
        contacts = family_contacts(profile)
        if not contacts:
            return AlertOutcome()
        body_hash = hashlib.sha256(result.message.body.encode()).hexdigest()
        token = secrets.token_urlsafe(16)
        alert_id, created = self.store.alert_create(uuid.uuid4().hex[:12], result.parent_id, result.id, body_hash, token)
        if not created:
            return AlertOutcome(alert_id, False, self._notified(alert_id))
        content = build_content(result, profile.name, alert_id, token, self.settings.public_base_url)
        for contact in contacts:
            self._alert_contact(alert_id, result.parent_id, contact, content)
        return AlertOutcome(alert_id, True, self._notified(alert_id))

    def _notified(self, alert_id: str) -> bool:
        alert = self.store.alert_get(alert_id)
        return bool(alert) and any(d["status"] == "sent" for d in alert["deliveries"])

    def _alert_contact(self, alert_id: str, parent_id: str, contact: Contact, content: AlertContent) -> None:
        s = self.settings
        if s.alert_max_per_contact > 0:
            since = datetime.now(timezone.utc) - timedelta(seconds=s.alert_window_seconds)
            if self.store.alerts_to_contact_since(contact.name, since) >= s.alert_max_per_contact:
                self.store.alert_delivery_add(
                    alert_id, contact.name, "all", "rate_limited",
                    detail=f"{s.alert_max_per_contact} alerts in the last {s.alert_window_seconds // 60} minutes",
                )
                return
        if contact.emails:
            self._send_email(alert_id, contact, content)
        self._send_telegram(alert_id, parent_id, contact, content)

    def _record(self, alert_id: str, contact: Contact, channel: str, status: str, target: str, detail: str, payload: dict) -> None:
        self.store.alert_delivery_add(alert_id, contact.name, channel, status, target=target, detail=detail, payload=payload)

    def _send_email(self, alert_id: str, contact: Contact, c: AlertContent) -> None:
        target = ", ".join(contact.emails)
        payload = {"subject": c.subject, "text": c.email_text, "html": c.email_html}
        if self.email is None:
            self._record(alert_id, contact, "email", "recorded", target, "RESEND_API_KEY / ALERT_EMAIL_FROM not set", payload)
            return
        try:
            self.email.send(contact.emails, c.subject, c.email_html, c.email_text, f"alert-{alert_id}-{contact.name}")
        except DeliveryError as exc:
            log.warning("email alert %s failed: %s", alert_id, exc)
            self._record(alert_id, contact, "email", "failed", target, str(exc), payload)
        else:
            self._record(alert_id, contact, "email", "sent", target, "", {"subject": c.subject})

    def _send_telegram(self, alert_id: str, parent_id: str, contact: Contact, c: AlertContent) -> None:
        chat = self.store.telegram_chat(parent_id, contact.name)
        payload = {"text": c.telegram, "buttons": self._buttons(alert_id, c)}
        if self.telegram is None:
            self._record(alert_id, contact, "telegram", "recorded", chat or "(not linked)", "TELEGRAM_BOT_TOKEN not set", payload)
            return
        if chat is None:
            self._record(alert_id, contact, "telegram", "skipped", "", "contact has not linked Telegram", {})
            return
        try:
            self.telegram.send_message(chat, c.telegram, payload["buttons"])
        except DeliveryError as exc:
            log.warning("telegram alert %s failed: %s", alert_id, exc)
            self._record(alert_id, contact, "telegram", "failed", chat, str(exc), payload)
        else:
            self._record(alert_id, contact, "telegram", "sent", chat, "", {})

    @staticmethod
    def _buttons(alert_id: str, c: AlertContent) -> list[list[dict]]:
        rows = [[{"text": "I called her", "callback_data": f"ack:{alert_id}"}]]
        # Telegram rejects url buttons that are not public https links (http://localhost, for one).
        if c.check_url and c.check_url.startswith("https://"):
            rows.append([{"text": "See the full check", "url": c.check_url}])
        return rows

    # --- acknowledgement ------------------------------------------------------------------------
    def acknowledge(self, alert_id: str, by: str, via: str) -> bool | None:
        """True when newly acknowledged, False when it already was, None for an unknown alert."""
        if self.store.alert_get(alert_id) is None:
            return None
        return self.store.alert_ack(alert_id, by, via)

    def acknowledge_with_token(self, alert_id: str, token: str, via: str = "email") -> bool | None:
        alert = self.store.alert_get(alert_id)
        if alert is None or not hmac.compare_digest(alert["token"], token or ""):
            return None
        return self.store.alert_ack(alert_id, "email link", via)

    def token_valid(self, alert_id: str, token: str) -> bool:
        alert = self.store.alert_get(alert_id)
        return bool(alert) and hmac.compare_digest(alert["token"], token or "")

    # --- telegram: linking and the webhook ------------------------------------------------------
    def bot_username(self) -> str | None:
        if self.telegram is None:
            return None
        if self.settings.telegram_bot_username:
            return self.settings.telegram_bot_username
        if self._bot_username is None:
            try:
                self._bot_username = self.telegram.bot_username()
            except DeliveryError as exc:
                log.warning("telegram getMe failed: %s", exc)
        return self._bot_username

    def create_telegram_link(self, profile: Profile, contact_name: str) -> dict | None:
        """A one-time code (valid 7 days) the family member opens as t.me/<bot>?start=<code>. None when the
        name is not one of the parent's family contacts."""
        contact = next((c for c in family_contacts(profile) if c.name.lower() == contact_name.strip().lower()), None)
        if contact is None:
            return None
        code = secrets.token_urlsafe(12)
        self.store.telegram_link_create(code, profile.id, contact.name)
        username = self.bot_username()
        return {
            "contact": contact.name,
            "code": code,
            "url": f"https://t.me/{username}?start={code}" if username else None,
            "bot_configured": self.telegram is not None,
        }

    def register_webhook(self) -> bool:
        """setWebhook, only when the token, the webhook secret and an https public URL are all configured."""
        s = self.settings
        if self.telegram is None or not s.telegram_webhook_secret or not (s.public_base_url or "").startswith("https://"):
            return False
        try:
            self.telegram.set_webhook(f"{s.public_base_url}/api/telegram/webhook", s.telegram_webhook_secret)
        except DeliveryError as exc:
            log.warning("telegram setWebhook failed: %s", exc)
            return False
        return True

    def webhook_secret_ok(self, header: str | None) -> bool:
        secret = self.settings.telegram_webhook_secret
        return bool(secret) and hmac.compare_digest(secret, header or "")

    def handle_update(self, update: dict) -> None:
        """Process one Telegram update: ``/start <code>`` links a chat, an "I called her" press acknowledges."""
        try:
            if "callback_query" in update:
                self._on_callback(update["callback_query"])
            elif "message" in update:
                self._on_message(update["message"])
        except DeliveryError as exc:
            log.warning("telegram update handling failed: %s", exc)

    def _on_message(self, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not chat_id or not text.startswith("/start") or self.telegram is None:
            return
        code = text[len("/start"):].strip()
        claimed = self.store.telegram_link_claim(code, chat_id, LINK_CODE_TTL) if code else None
        if claimed is None:
            self.telegram.send_message(chat_id, "This link has expired or was already used. Ask for a new ScamShield link.")
            return
        parent_id, contact = claimed
        profile = self.store.get_profile(parent_id)
        name = html.escape(profile.name.split()[0] if profile and profile.name.strip() else "your parent")
        self.telegram.send_message(
            chat_id,
            f"✅ You're linked, {html.escape(contact.split()[0])}. ScamShield will message you here when {name} gets a message that looks dangerous.",
        )

    def _on_callback(self, cb: dict) -> None:
        if self.telegram is None:
            return
        data = cb.get("data") or ""
        message = cb.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        if not data.startswith("ack:"):
            self.telegram.call("answerCallbackQuery", callback_query_id=cb.get("id"))
            return
        alert_id = data[len("ack:"):]
        linked = self.store.telegram_contact_for_chat(chat_id)
        alert = self.store.alert_get(alert_id)
        # Only a chat linked to this alert's parent may acknowledge it.
        if linked is None or alert is None or linked[0] != alert["parent_id"]:
            self.telegram.call("answerCallbackQuery", callback_query_id=cb.get("id"), text="This chat is not linked to that alert.", show_alert=True)
            return
        self.store.alert_ack(alert_id, linked[1], "telegram")
        self.telegram.call("answerCallbackQuery", callback_query_id=cb.get("id"), text="Thanks, marked as called.")
        if message.get("message_id"):
            self.telegram.call(
                "editMessageReplyMarkup",
                chat_id=chat_id,
                message_id=message["message_id"],
                reply_markup={"inline_keyboard": [[{"text": "✓ You called her", "callback_data": "noop"}]]},
            )


def alert_view(alert: dict) -> dict:
    """The alert as the API returns it: no ack token."""
    return {k: v for k, v in alert.items() if k != "token"}
