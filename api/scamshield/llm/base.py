from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    pass


@dataclass
class LLMRequest:
    task: str  # "triage" | "plan" | "verdict"
    model: str
    system: str
    user: str
    # Structured inputs behind the prompt. Real clients ignore it; the fake answers from it.
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    data: dict[str, Any]
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float


class LLMClient(Protocol):
    name: str
    offline: bool

    def complete_json(self, request: LLMRequest) -> LLMResponse: ...
