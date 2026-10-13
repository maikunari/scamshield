from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    pass


class LLMBlocked(LLMError):
    """The budget guard refused the call; nothing was sent."""


@dataclass
class LLMRequest:
    task: str  # "triage" | "plan" | "verdict"
    model: str
    system: str
    user: str
    # Structured inputs behind the prompt. Real clients ignore it; the fake answers from it.
    context: dict[str, Any] = field(default_factory=dict)
    # JSON Schema of the reply. Real clients send it as ``response_format`` and repeat it in the prompt.
    schema: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    data: dict[str, Any]
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    mode: str = "json_schema"  # how the reply was obtained: json_schema | json_object | repair | fake


class LLMClient(Protocol):
    name: str
    offline: bool

    def complete_json(self, request: LLMRequest) -> LLMResponse: ...
