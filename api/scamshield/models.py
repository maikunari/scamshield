"""Data shapes shared across the pipeline. All are plain pydantic models (JSON-serialisable)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["sms", "email", "voicemail", "paste"]
Label = Literal["green", "yellow", "red"]
Risk = Literal["none", "low", "medium", "high"]
Impersonation = Literal[
    "none", "bank", "delivery", "family", "police", "government", "tech_support", "utility", "other"
]


class RawMessage(BaseModel):
    """What an intake channel hands to the pipeline."""

    body: str
    sender: str | None = None
    channel: Channel = "paste"
    subject: str | None = None
    received_at: str | None = None  # ISO 8601; defaults to now


class Message(BaseModel):
    """Step 1 output: ``Message{channel, sender, body, urls[], phones[], ts}``."""

    channel: Channel
    sender: str | None = None  # normalised: E.164 phone, lowercase email, or raw string
    subject: str | None = None
    body: str
    urls: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)  # E.164 where determinable
    ts: str


class Signals(BaseModel):
    urgency: bool = False
    money_request: bool = False
    impersonation: Impersonation = "none"
    secrecy_request: bool = False
    credential_request: bool = False  # asks to log in, verify, claim, enter details or call back
    link_present: bool = False
    phone_present: bool = False


class Triage(BaseModel):
    """Step 2 output (Nemotron Nano)."""

    signals: Signals = Field(default_factory=Signals)
    risk: Risk = "none"
    scam_type: str | None = None
    key_phrase: str | None = None
    language: str = "en"
    summary: str = ""


class ContactMatch(BaseModel):
    """Step 3 output: how the sender relates to the parent's profile."""

    status: Literal["trusted", "known_service", "unknown"] = "unknown"
    name: str | None = None
    relationship: str | None = None
    reason: str = ""
    lookalike_of: str | None = None  # known service whose address this sender or link imitates


class SearchHit(BaseModel):
    title: str = ""
    url: str
    content: str = ""
    excerpt: str = ""  # page text from the one Tavily extract allowed per message, when it was used


class Lookup(BaseModel):
    """One Tavily skill invocation and what it found."""

    skill: Literal["check_phone", "check_url", "check_phrase"]
    value: str
    query: str
    topic: str = "general"
    hits: list[SearchHit] = Field(default_factory=list)  # relevance-filtered
    raw_hit_count: int = 0
    cached: bool = False
    skipped: str | None = None  # e.g. "budget exhausted"


class RedFlag(BaseModel):
    text: str
    evidence_url: str | None = None


class Verdict(BaseModel):
    label: Label
    score: int = Field(ge=0, le=100)
    red_flags: list[RedFlag] = Field(default_factory=list)
    safe_action: str = ""
    family_script: str = ""
    evidence_note: str = ""
    degraded: bool = False  # True when the verdict model failed and rules produced the verdict


class TraceStep(BaseModel):
    name: str
    kind: Literal["model", "tool", "local"] = "local"
    tier: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    detail: str = ""
    error: str | None = None


class Trace(BaseModel):
    steps: list[TraceStep] = Field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens_in + s.tokens_out for s in self.steps)

    @property
    def total_latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.steps)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)


class AnalysisResult(BaseModel):
    id: str
    parent_id: str
    created_at: str
    message: Message
    triage: Triage
    contact: ContactMatch
    lookups: list[Lookup] = Field(default_factory=list)
    verdict: Verdict
    trace: Trace
    offline: bool = False
    budget_mode: str = "normal"  # normal | degraded (Nano-only) | exhausted | killed
    cached_from: str | None = None  # id of the earlier analysis this verdict was served from
    demo: bool = False  # a sample check loaded with the demo family; its trace is illustrative, not a recorded run


def label_for(score: int) -> Label:
    if score >= 70:
        return "red"
    if score >= 30:
        return "yellow"
    return "green"
