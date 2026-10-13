import json

import httpx

from scamshield.config import Settings
from scamshield.evaluation import run_eval
from scamshield.llm import FakeLLMClient, LLMError, LLMRequest, LLMResponse
from scamshield.memory import MemoryStore
from scamshield.models import RawMessage
from scamshield.pipeline import Pipeline
from scamshield.runtime import build_pipeline, load_seed_messages
from scamshield.search import FakeSearchClient, TavilyClient

SEED = dict(load_seed_messages(Settings.from_env({}).seed_dir))


def run(pipeline, mid):
    return pipeline.analyze(SEED[mid], "michiko")


def test_scam_gets_red_with_real_evidence_urls(pipeline, search):
    r = run(pipeline, "m10")
    v = r.verdict
    assert v.label == "red" and v.score >= 70 and v.safe_action and v.family_script
    hit_urls = {h.url for lk in r.lookups for h in lk.hits}
    cited = [f.evidence_url for f in v.red_flags if f.evidence_url]
    assert cited and set(cited) <= hit_urls
    assert v.evidence_note.startswith("Public reports found")
    assert len([lk for lk in r.lookups]) <= 3


def test_nothing_found_says_so_and_cites_nothing(pipeline):
    r = run(pipeline, "m14")  # new-number "Taro": no fixtures for it
    assert r.verdict.label == "red"
    assert r.verdict.evidence_note == "No public reports found."
    assert all(f.evidence_url is None for f in r.verdict.red_flags)


def test_trusted_contact_skips_model_and_search(pipeline, search):
    r = run(pipeline, "m17")
    assert r.verdict.label == "green" and r.contact.status == "trusted"
    assert [s.name for s in r.trace.steps if s.kind in ("tool",) or s.name in ("plan", "verdict")] == []
    assert search.calls == []


def test_same_grandson_story_from_unknown_number_is_red(pipeline):
    taro_real = pipeline.analyze(RawMessage(body="Grandma it's Taro, can you send $50 for a book?", sender="212-555-0102"), "michiko")
    taro_fake = pipeline.analyze(RawMessage(body="Grandma it's Taro, can you send $50 for a book?", sender="212-555-0173"), "michiko")
    assert taro_real.verdict.score < taro_fake.verdict.score
    assert taro_fake.verdict.score >= 50
    assert taro_real.verdict.score >= 30  # trusted but money ask: at least careful


def test_unknown_family_plus_money_floor_applies_even_if_model_says_low(settings, store, search):
    class Lenient(FakeLLMClient):
        def complete_json(self, request: LLMRequest) -> LLMResponse:
            r = super().complete_json(request)
            if request.task == "verdict":
                r.data["score"] = 5
            return r

    p = Pipeline(settings, Lenient(), search, store)
    r = run(p, "m14")
    assert r.verdict.score == 75 and r.verdict.label == "red"
    assert any("floor 75" in s.detail for s in r.trace.steps)


def test_invented_evidence_urls_are_stripped(settings, store, search):
    class Liar(FakeLLMClient):
        def complete_json(self, request):
            r = super().complete_json(request)
            if request.task == "verdict":
                r.data["red_flags"] = [{"text": "Reported 500 times", "evidence_url": "https://made-up.example.com/report"}]
            return r

    r = run(Pipeline(settings, Liar(), search, store), "m14")
    assert r.verdict.red_flags[0].evidence_url is None
    assert any("removed 1 evidence url" in s.detail for s in r.trace.steps)


def test_search_budget_is_three_calls_per_message(pipeline, search):
    r = pipeline.analyze(RawMessage(
        body="Urgent: pay the fee at http://a.test/1 and http://b.test/2 or call 212-555-0101 / 212-555-0102 / 212-555-0103",
        sender="212-555-0199"), "michiko")
    assert len(search.calls) <= 3
    assert len(r.lookups) <= 3


def test_pii_never_reaches_the_model(settings, store, search):
    seen = []

    class Spy(FakeLLMClient):
        def complete_json(self, request):
            seen.append(request.system + request.user)
            return super().complete_json(request)

    body = "Urgent pay now, card 4111 1111 1111 1111 password: hunter2 SSN 123-45-6789"
    Pipeline(settings, Spy(), search, store).analyze(RawMessage(body=body, sender="212-555-0199"), "michiko")
    blob = "\n".join(seen)
    assert seen and "4111" not in blob and "hunter2" not in blob and "6789" not in blob


def test_model_failure_degrades_to_careful(settings, store, search):
    class Down(FakeLLMClient):
        def complete_json(self, request):
            if request.task == "verdict":
                raise LLMError("boom")
            return super().complete_json(request)

    r = run(Pipeline(settings, Down(), search, store), "m02")
    assert r.verdict.degraded and r.verdict.label in ("yellow", "red")
    assert [s.error for s in r.trace.steps if s.name == "verdict"] == ["boom"]


def test_triage_failure_still_runs_evidence_and_verdict(settings, store, search):
    class Down(FakeLLMClient):
        def complete_json(self, request):
            if request.task == "triage":
                raise LLMError("nope")
            return super().complete_json(request)

    r = run(Pipeline(settings, Down(), search, store), "m02")
    assert r.triage.risk == "medium" and r.verdict.label != "green"


def test_trace_records_model_tokens_latency_and_cost(pipeline):
    r = run(pipeline, "m02")
    models = {s.name: s for s in r.trace.steps if s.kind == "model"}
    assert set(models) == {"triage", "plan", "verdict"}
    assert (models["triage"].tier, models["plan"].tier, models["verdict"].tier) == ("fast", "mid", "reason")
    assert all(s.model and s.tokens_in > 0 and s.cost_usd > 0 for s in models.values())
    assert r.trace.total_cost_usd > 0 and r.trace.total_tokens > 0
    assert pipeline.store.get_analysis(r.id).trace.total_tokens == r.trace.total_tokens


def test_url_under_suspicion_is_never_fetched(settings, store):
    hosts = []

    def handler(request: httpx.Request):
        hosts.append(request.url.host)
        return httpx.Response(200, json={"results": []})

    tavily = TavilyClient("k", transport=httpx.MockTransport(handler))
    r = Pipeline(settings, FakeLLMClient(), tavily, store).analyze(SEED["m02"], "michiko")
    assert r.lookups and set(hosts) == {"api.tavily.com"}


def test_seed_eval_has_no_dangerous_misses_and_good_accuracy():
    pipeline = build_pipeline(Settings.from_env({"SCAMSHIELD_OFFLINE": "1", "SCAMSHIELD_DB": ":memory:"}))
    report = run_eval(pipeline)
    assert len(report.rows) >= 25
    assert report.dangerous_misses == []
    assert report.accuracy >= 0.9
    assert report.per_class()["green"]["precision"] == 1.0


def test_api_analyze_roundtrip(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
    monkeypatch.setenv("SCAMSHIELD_DB", ":memory:")
    from scamshield import main

    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()
    client = TestClient(main.app)
    assert client.get("/health").json()["offline"] is True
    body = {"body": "Final notice: pay $212 with a prepaid card today", "sender": "212-555-0121", "channel": "sms"}
    res = client.post("/parents/p1/analyze", json=body).json()
    assert res["verdict"]["label"] == "red"
    assert client.get(f"/results/{res['id']}").json()["id"] == res["id"]
    assert client.get("/results/nope").status_code == 404
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()
