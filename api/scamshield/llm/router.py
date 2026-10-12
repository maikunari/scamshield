from __future__ import annotations

from ..config import Settings
from ..models import Trace, TraceStep
from .base import LLMClient, LLMRequest, LLMResponse


class ModelRouter:
    """Routes a pipeline step to its tier (fast=Nano, mid=Super, reason=Ultra), calls the client and logs a trace step."""

    TASK_TIER = {"triage": "fast", "plan": "mid", "verdict": "reason"}

    def __init__(self, client: LLMClient, settings: Settings):
        self.client = client
        self.settings = settings

    def cost(self, tier: str, tokens_in: int, tokens_out: int) -> float:
        price_in, price_out = self.settings.prices[tier]
        return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)

    def call(self, task: str, system: str, user: str, context: dict, trace: Trace) -> LLMResponse:
        """Run one step. Always appends a trace step (with ``error`` set on failure) and re-raises failures."""
        tier = self.TASK_TIER[task]
        model = self.settings.model_for(tier)
        request = LLMRequest(task=task, model=model, system=system, user=user, context=context)
        try:
            response = self.client.complete_json(request)
        except Exception as exc:
            trace.steps.append(
                TraceStep(name=task, kind="model", tier=tier, model=model, detail=f"{tier} tier call failed", error=str(exc))
            )
            raise
        trace.steps.append(
            TraceStep(
                name=task,
                kind="model",
                tier=tier,
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
                cost_usd=self.cost(tier, response.tokens_in, response.tokens_out),
            )
        )
        return response
