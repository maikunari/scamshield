"""Step 1: turn a raw intake payload into a ``Message`` with extracted urls and phones."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import Message, RawMessage

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'）」』】、。]+", re.IGNORECASE)
# Digit runs with common separators; the digit count is checked after cleaning.
PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d[\d\-\s().]{6,}\d(?![\w])")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TRAILING = ".,;:!?)]}>'\""


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(TRAILING)
        if url.lower().startswith("www."):
            url = "http://" + url
        if url not in seen:
            seen.append(url)
    return seen


def domain_of(url: str) -> str:
    host = urlparse(url if "://" in url else "http://" + url).hostname or ""
    return host.lower().removeprefix("www.")


def to_e164(raw: str) -> str | None:
    """Best-effort E.164. ``+`` numbers are kept; bare 10-digit numbers are treated as US and
    numbers with a leading 0 as Japanese. Returns None when it does not look like a phone."""
    raw = raw.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10 and not digits.startswith("0"):
        return f"+1{digits}"
    if len(digits) in (10, 11) and digits.startswith("0"):
        return f"+81{digits[1:]}"
    return None


def extract_phones(text: str) -> list[str]:
    seen: list[str] = []
    cleaned = URL_RE.sub(" ", text)
    for m in PHONE_RE.finditer(cleaned):
        e164 = to_e164(m.group(0))
        if e164 and e164 not in seen:
            seen.append(e164)
    return seen


def normalize_sender(sender: str | None) -> str | None:
    if not sender:
        return None
    sender = sender.strip()
    m = re.search(r"<([^>]+)>", sender)
    if m:
        sender = m.group(1).strip()
    if EMAIL_RE.match(sender):
        return sender.lower()
    return to_e164(sender) or sender


def normalize(raw: RawMessage) -> Message:
    text = f"{raw.subject}\n{raw.body}" if raw.subject else raw.body
    ts = raw.received_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return Message(
        channel=raw.channel,
        sender=normalize_sender(raw.sender),
        subject=raw.subject,
        body=raw.body.strip(),
        urls=extract_urls(text),
        phones=extract_phones(text),
        ts=ts,
    )


def phone_variants(e164: str) -> list[str]:
    """E.164 plus local spellings, used when searching for reports about a number."""
    digits = e164.lstrip("+")
    variants = [e164]
    if e164.startswith("+1") and len(digits) == 11:
        n = digits[1:]
        variants += [f"{n[:3]}-{n[3:6]}-{n[6:]}", f"({n[:3]}) {n[3:6]}-{n[6:]}"]
    elif e164.startswith("+81"):
        n = "0" + digits[2:]
        variants += [f"{n[:3]}-{n[3:7]}-{n[7:]}" if len(n) == 11 else n]
    return variants
