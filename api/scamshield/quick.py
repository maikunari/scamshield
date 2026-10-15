"""Instant answers for obvious cases, before any model or search call.

A quick answer is only a preview shown while the full check runs, and it can only point one of two ways:

* **escalate**: a well-known scam script (gift card codes, bail money, a "suspended" Social Security number, an
  unpaid toll...) or a look-alike of a known service's address, from a sender who is not a trusted contact;
* **confirm a trusted-contact green**: the sender is in the parent's trusted contacts and nothing in the message
  asks for money, codes or secrecy.

It never says "safe" for anyone else. The full check always completes and its verdict is the answer, even when it
disagrees with the preview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ContactMatch, Label, Message

_F = re.IGNORECASE | re.DOTALL

# (what the parent is told, pattern). High precision on purpose: a miss only means no preview.
KNOWN_SCAMS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Asking for gift card codes is a well-known scam.",
        re.compile(r"gift ?cards?\b.{0,120}\b(?:codes?|numbers?|pins?)\b|\b(?:codes?|pins?)\b.{0,60}gift ?cards?", _F),
    ),
    ("A relative asking for bail money is a well-known scam.", re.compile(r"\bbail\b.{0,120}(?:\$|money|pay|send)|(?:\$|money).{0,80}\bbail\b", _F)),
    (
        "A \"suspended\" Social Security number is a well-known scam.",
        re.compile(r"social security (?:number|card|account)[^.]{0,60}\b(?:suspended|blocked|frozen|compromised)", _F),
    ),
    ("Threatening an arrest warrant is a well-known scam.", re.compile(r"warrant[^.]{0,60}\barrest|arrest warrant", _F)),
    ("Texts about an unpaid toll are a well-known scam.", re.compile(r"\b(?:unpaid|outstanding|overdue)\s+toll", _F)),
    ("A parcel redelivery fee is a well-known scam.", re.compile(r"re-?delivery fee", _F)),
    (
        "A bank \"fraud alert\" asking you to call a number is a well-known scam.",
        re.compile(r"fraud alert.{0,240}\bcall\b|\bcall\b.{0,80}\b(?:security|fraud) (?:department|team)", _F),
    ),
    (
        "Paying by Bitcoin ATM or wire transfer is how scammers ask for money.",
        re.compile(r"bitcoin atm|crypto(?:currency)? atm|western union|wire (?:the )?money", _F),
    ),
    ("A prize that needs a fee first is a well-known scam.", re.compile(r"(?:you(?:'ve| have) won|been selected).{0,200}\bfee\b", _F)),
    ("還付金をATMで受け取らせるのは詐欺の手口です。", re.compile(r"還付金.{0,60}ATM|ATM.{0,60}還付金", _F)),
]

PHRASE_WORDS = 8

# Anything that asks for money, codes or secrecy keeps a trusted contact's message out of the quick green.
_ASKS = re.compile(
    r"\$\s?\d|money|\bpay|\bbail\b|gift ?card|\bwire\b|transfer|\bloan\b|bank|password|\bcode\b|\bpin\b|"
    r"don'?t tell|do not tell|secret|between us|振り?込|送金|お金|暗証|内緒|秘密",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuickAnswer:
    label: Label  # "red" (escalate) or "green" (trusted contact only)
    reason: str  # one sentence the parent can read
    phrase: str | None = None  # the matched scam wording: the phrase lookup can start before triage picks one


def quick_check(message: Message, contact: ContactMatch) -> QuickAnswer | None:
    text = f"{message.subject or ''}\n{message.body}"
    if contact.status == "trusted":
        if _ASKS.search(text) or message.urls:
            return None
        who = f"{contact.name}, your {contact.relationship}," if contact.relationship else contact.name
        return QuickAnswer("green", f"It came from {who} who is in your trusted contacts.")
    if contact.lookalike_of:
        return QuickAnswer("red", f"The address looks like {contact.lookalike_of}'s, but it is not the real one.")
    for reason, pattern in KNOWN_SCAMS:
        if match := pattern.search(message.body):
            return QuickAnswer("red", reason, _phrase(match.group(0)))
    if message.subject:
        for reason, pattern in KNOWN_SCAMS:
            if pattern.search(message.subject):
                return QuickAnswer("red", reason)
    return None


def _phrase(matched: str, max_words: int = PHRASE_WORDS) -> str:
    """The matched wording as a search phrase: its first few words, without trailing punctuation."""
    return " ".join(matched.split()[:max_words]).strip(" .,:;!?")
