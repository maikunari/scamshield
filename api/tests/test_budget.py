import pytest

from scamshield.budget import DEGRADED, EXHAUSTED, KILLED, NORMAL, Budget, BudgetBlocked
from scamshield.config import Settings
from scamshield.llm import FakeLLMClient, LLMBlocked, LLMResponse, ModelRouter
from scamshield.memory import MemoryStore
from scamshield.models import RawMessage, Trace
from scamshield.pipeline import Pipeline
from scamshield.runtime import load_seed_messages


def live_settings(**env):
    return Settings.from_env({"NEBIUS_API_KEY": "k", "TAVILY_API_KEY": "t", **env})


class LiveFake(FakeLLMClient):
    """A fake that counts as a paid (non-offline) client, so the budget books its calls."""

    offline = False

    def __init__(self):
        self.models = []

    def complete_json(self, request):
        self.models.append(request.model)
        return super().complete_json(request)


def test_modes_follow_the_caps():
    store = MemoryStore(":memory:")
    s = live_settings(BUDGET_DAILY_ULTRA_CALLS="2", BUDGET_DAILY_ULTRA_USD="1", BUDGET_TOTAL_USD="10")
    b = Budget(store, s)
    assert b.mode == NORMAL
    b.record("model", "reason", 0.1)
    b.record("model", "reason", 0.1)
    st = b.status()
    assert (st.mode, st.ultra_calls_today) == (DEGRADED, 2) and "call cap" in st.reason
    store2 = MemoryStore(":memory:")
    b2 = Budget(store2, s)
    b2.record("model", "reason", 1.0)
    assert b2.status().mode == DEGRADED and "Ultra spend cap" in b2.status().reason
    store3 = MemoryStore(":memory:")
    b3 = Budget(store3, s)
    b3.record("model", "fast", 8.0)  # 80% of the total cap: Nano-only
    assert b3.mode == DEGRADED and "total spend cap" in b3.status().reason
    b3.record("tool", "tavily", 2.0)
    assert b3.mode == EXHAUSTED


def test_kill_switch_via_env_file_and_call(tmp_path):
    store = MemoryStore(":memory:")
    assert Budget(store, live_settings(BUDGET_KILL_SWITCH="1")).mode == KILLED
    flag = tmp_path / "kill"
    b = Budget(store, live_settings(BUDGET_KILL_FILE=str(flag)))
    assert b.mode == NORMAL
    flag.write_text("")
    assert b.mode == KILLED
    flag.unlink()
    assert b.mode == NORMAL
    b.kill()
    assert b.mode == KILLED
    b.revive()
    assert b.mode == NORMAL
    with pytest.raises(BudgetBlocked):
        Budget(store, live_settings(BUDGET_KILL_SWITCH="1")).plan_tier("reason")


def test_router_books_cost_at_catalog_prices_and_runs_ultra_when_normal():
    settings = live_settings()
    store = MemoryStore(":memory:")
    budget = Budget(store, settings)
    client = LiveFake()
    router = ModelRouter(client, settings, budget)
    trace = Trace()
    resp = LLMResponse(data={"score": 1}, model="x", tokens_in=1_000_000, tokens_out=1_000_000, latency_ms=1)
    client.complete_json = lambda request: resp
    router.call("verdict", "s", "u", {}, trace)
    step = trace.steps[0]
    assert (step.tier, step.cost_usd) == ("reason", 4.0)  # Ultra: $1 in + $3 out per 1M
    assert budget.store.spend_today("reason") == (1, 4.0)
    router.call("triage", "s", "u", {}, trace)
    assert trace.steps[1].cost_usd == pytest.approx(0.30)  # Nano: $0.06 + $0.24


def test_degraded_mode_runs_every_step_on_nano():
    settings = live_settings(BUDGET_DAILY_ULTRA_CALLS="1")
    store = MemoryStore(":memory:")
    budget = Budget(store, settings)
    budget.record("model", "reason", 0.01)
    client = LiveFake()
    client.complete_json = lambda request: LLMResponse(data={}, model=request.model, tokens_in=10, tokens_out=10, latency_ms=1)
    trace = Trace()
    ModelRouter(client, settings, budget).call("verdict", "s", "u", {}, trace)
    step = trace.steps[0]
    assert step.tier == "fast" and "Nano" in settings.model_for("fast") and "degraded" in step.detail
    assert budget.store.spend_today("reason")[0] == 1  # the Nano call was not booked as an Ultra call


def test_blocked_call_raises_and_leaves_a_trace_step():
    settings = live_settings(BUDGET_KILL_SWITCH="1")
    router = ModelRouter(LiveFake(), settings, Budget(MemoryStore(":memory:"), settings))
    trace = Trace()
    with pytest.raises(LLMBlocked):
        router.call("triage", "s", "u", {}, trace)
    assert trace.steps[0].error and "kill switch" in trace.steps[0].error


SEED = dict(load_seed_messages(Settings.from_env({}).seed_dir))


def test_pipeline_in_degraded_mode_uses_only_nano_and_still_searches(store, search):
    settings = live_settings(BUDGET_DAILY_ULTRA_CALLS="0")
    llm = LiveFake()
    r = Pipeline(settings, llm, search, store).analyze(SEED["m02"], "margaret", use_cache=False)
    assert r.budget_mode == "degraded"
    assert {s.tier for s in r.trace.steps if s.kind == "model"} == {"fast"}
    assert set(llm.models) == {settings.model_for("fast")}
    assert r.verdict.label == "red" and r.lookups and any(s.name == "budget" for s in r.trace.steps)


def test_pipeline_with_kill_switch_makes_no_paid_calls_and_says_so(store, search):
    llm = LiveFake()
    r = Pipeline(live_settings(BUDGET_KILL_SWITCH="1"), llm, search, store).analyze(SEED["m02"], "margaret")
    assert llm.models == [] and search.calls == []
    assert r.budget_mode == "killed" and r.verdict.degraded and r.verdict.label == "yellow"
    assert "ask your family" in r.verdict.safe_action.lower()
    assert all(lk.skipped and "budget guard" in lk.skipped for lk in r.lookups)


def test_kill_switch_never_calls_a_message_safe_even_when_everything_is_refused(store, search):
    p = Pipeline(live_settings(BUDGET_KILL_SWITCH="1"), LiveFake(), search, store)
    r = p.analyze(RawMessage(body="Your parcel fee is unpaid, pay now", sender="212-555-0199"), "margaret")
    assert r.verdict.score >= 30


def test_live_spend_is_booked_and_offline_spend_is_not(settings, store, search):
    live = Pipeline(live_settings(), LiveFake(), search, store)
    live.analyze(SEED["m02"], "margaret", use_cache=False)
    assert store.spend_total() > 0 and store.spend_today("reason")[0] == 1
    before = store.spend_total()
    Pipeline(settings, FakeLLMClient(), search, store).analyze(SEED["m10"], "margaret", use_cache=False)
    assert store.spend_total() == before


def test_total_cap_blocks_the_next_message(store, search):
    settings = live_settings(BUDGET_TOTAL_USD="0.001")
    p = Pipeline(settings, LiveFake(), search, store)
    first = p.analyze(SEED["m02"], "margaret", use_cache=False)
    assert first.budget_mode == "normal"
    second = p.analyze(SEED["m10"], "margaret", use_cache=False)
    assert second.budget_mode == "exhausted" and second.verdict.degraded
