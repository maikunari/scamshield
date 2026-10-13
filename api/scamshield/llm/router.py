from __future__ import annotations

from ..budget import Budget, BudgetBlocked
from ..config import Settings
from ..models import Trace, TraceStep
from .base import LLMBlocked, LLMClient, LLMRequest, LLMResponse
from .schemas import SCHEMAS


class ModelRouter:
    """Routes a pipeline step to its tier (fast=Nano, mid=Super, reason=Ultra), calls the client and logs a trace step.

    With a ``Budget`` it also books each live call's cost, runs the step on Nano when the budget is degraded and
    refuses the call (``LLMBlocked``) when the budget is exhausted or killed.
    """

    TASK_TIER = {"triage": "fast", "plan": "mid", "verdict": "reason"}

    def __init__(self, client: LLMClient, settings: Settings, budget: Budget | None = None):
        self.client = client
        self.settings = settings
        self.budget = budget

    def cost(self, tier: str, model: str, tokens_in: int, tokens_out: int) -> float:
        """USD for one call, at the listed per-1M-token prices of ``model`` (case-insensitive id match)."""
        price_in, price_out = self.settings.price_for(tier, model)
        return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)

    def call(self, task: str, system: str, user: str, context: dict, trace: Trace) -> LLMResponse:
        """Run one step. Always appends a trace step (with ``error`` set on failure) and re-raises failures."""
        wanted = self.TASK_TIER[task]
        tier, note = wanted, ""
        if self.budget:
            try:
                tier = self.budget.plan_tier(wanted)
            except BudgetBlocked as exc:
                trace.steps.append(
                    TraceStep(name=task, kind="model", tier=wanted, detail="budget guard: no model call", error=str(exc))
                )
                raise LLMBlocked(str(exc)) from exc
            if tier != wanted:
                note = f"budget degraded: {wanted} -> {tier} tier"
        model = self.settings.model_for(tier)
        request = LLMRequest(task=task, model=model, system=system, user=user, context=context, schema=SCHEMAS.get(task))
        try:
            response = self.client.complete_json(request)
        except Exception as exc:
            trace.steps.append(
                TraceStep(name=task, kind="model", tier=tier, model=model, detail=note or f"{tier} tier call failed", error=str(exc))
            )
            raise
        cost = self.cost(tier, model, response.tokens_in, response.tokens_out)
        if self.budget and not self.client.offline:
            self.budget.record("model", tier, cost)
        detail = ", ".join(x for x in (note, f"output via {response.mode}" if response.mode not in ("json_schema", "fake") else "") if x)
        trace.steps.append(
            TraceStep(
                name=task,
                kind="model",
                tier=tier,
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
                cost_usd=cost,
                detail=detail,
            )
        )
        return response
