"""PII redaction. Runs on every message before any model call (never on the stored raw body)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CARD_RE = re.compile(r"(?<![\d+])(?:\d[ -]?){13,19}(?!\d)")
MY_NUMBER_RE = re.compile(r"(?<![\d+])\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")  # JP My Number: 12 digits
SSN_RE = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
SECRET_RE = re.compile(
    r"(?P<key>(?<![A-Za-z])(?:password|passcode|passphrase|pin|cvv|cvc)(?![A-Za-z])|パスワード|暗証番号|認証コード)"
    r"(?P<sep>\s*(?:is|are|:|：|=|は)\s*)(?P<val>[^\s,.;、。]+)",
    re.IGNORECASE,
)


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


@dataclass
class Redaction:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def redact(text: str) -> Redaction:
    counts: dict[str, int] = {}

    def bump(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            bump("card_number")
            return "[REDACTED_CARD]"
        return m.group(0)

    def secret(m: re.Match[str]) -> str:
        bump("password")
        return f"{m.group('key')}{m.group('sep')}[REDACTED_SECRET]"

    out = CARD_RE.sub(card, text)
    out = SSN_RE.sub(lambda m: (bump("ssn"), "[REDACTED_SSN]")[1], out)
    out = MY_NUMBER_RE.sub(lambda m: (bump("my_number"), "[REDACTED_ID]")[1], out)
    out = SECRET_RE.sub(secret, out)
    return Redaction(out, counts)
