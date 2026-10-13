import json

import httpx
import pytest
from fastapi.testclient import TestClient

from scamshield.config import Settings
from scamshield.llm import FakeLLMClient, LLMError
from scamshield.memory import MemoryStore
from scamshield.models import RawMessage
from scamshield.pipeline import Pipeline
from scamshield.ratelimit import RateLimiter, client_ip
from scamshield.runtime import load_seed_messages
from scamshield.search import FakeSearchClient, TavilyClient

SEED = dict(load_seed_messages(Settings.from_env({}).seed_dir))


class Counting(FakeLLMClient):
    def __init__(self):
        self.calls = 0

    def complete_json(self, request):
        self.calls += 1
        return super().complete_json(request)


def test_second_run_of_a_message_is_free_and_identical(settings, store, search):
    llm = Counting()
    p = Pipeline(settings, llm, search, store)
    first = p.analyze(SEED["m10"], "michiko")
    calls, searches = llm.calls, len(search.calls)
    second = p.analyze(SEED["m10"], "michiko")
    assert (llm.calls, len(search.calls)) == (calls, searches)
    assert second.cached_from == first.id and second.id != first.id
    assert (second.verdict, second.triage, second.lookups) == (first.verdict, first.triage, first.lookups)
    assert second.trace.total_cost_usd == 0 and second.trace.total_tokens == 0
    assert [s.name for s in second.trace.steps] == ["normalize", "verdict_cache"]
    assert store.get_analysis(second.id).cached_from == first.id


def test_use_cache_false_forces_a_fresh_run(settings, store, search):
    llm = Counting()
    p = Pipeline(settings, llm, search, store)
    p.analyze(SEED["m10"], "michiko")
    n = llm.calls
    fresh = p.analyze(SEED["m10"], "michiko", use_cache=False)
    assert llm.calls > n and fresh.cached_from is None


def test_cache_is_keyed_by_message_sender_profile_and_models(settings, store, search, profile):
    llm = Counting()
    p = Pipeline(settings, llm, search, store)
    body = "Final notice: pay the $40 fee by gift card today"
    a = p.analyze(RawMessage(body=body, sender="212-555-0199"), "michiko")
    n = llm.calls
    assert p.analyze(RawMessage(body="  " + body.replace(" ", "  ") + " ", sender="212-555-0199"), "michiko").cached_from == a.id  # whitespace-insensitive
    assert llm.calls == n
    assert p.analyze(RawMessage(body=body, sender="212-555-0198"), "michiko").cached_from is None  # other sender
    assert p.analyze(RawMessage(body=body + "!", sender="212-555-0199"), "michiko").cached_from is None  # other text
    profile.trusted_contacts[0].phones.append("212-555-0199")  # the profile changed: the old verdict is stale
    store.save_profile(profile)
    assert p.analyze(RawMessage(body=body, sender="212-555-0199"), "michiko").cached_from is None
    live = Pipeline(Settings.from_env({"NEBIUS_API_KEY": "k"}), llm, search, store)
    other = Pipeline(Settings.from_env({"NEBIUS_API_KEY": "k", "MODEL_REASON": "x/other"}), llm, search, store)
    same_but_cased = Pipeline(Settings.from_env({"NEBIUS_API_KEY": "k", "MODEL_REASON": "NVIDIA/NEMOTRON-3-ULTRA-550B-A55B"}), llm, search, store)
    assert other.cache_key(a.message, profile) != live.cache_key(a.message, profile)
    assert same_but_cased.cache_key(a.message, profile) == live.cache_key(a.message, profile)  # id casing does not matter


def test_failed_or_degraded_runs_are_not_cached(settings, store, search):
    class Down(FakeLLMClient):
        def complete_json(self, request):
            if request.task == "verdict":
                raise LLMError("boom")
            return super().complete_json(request)

    r = Pipeline(settings, Down(), search, store).analyze(SEED["m02"], "michiko")
    assert r.verdict.degraded
    healthy = Pipeline(settings, Counting(), search, store).analyze(SEED["m02"], "michiko")
    assert healthy.cached_from is None and not healthy.verdict.degraded


def test_green_cached_body_is_not_stored_but_the_reply_has_it(settings, store, search):
    p = Pipeline(settings, Counting(), search, store)
    msg = RawMessage(body="Mom, I'll call you Sunday.", sender="212-555-0101")
    p.analyze(msg, "michiko")
    again = p.analyze(msg, "michiko")
    assert again.cached_from and again.message.body.startswith("Mom")
    assert store.get_analysis(again.id).message.body == ""
    row = store._db.execute("SELECT result_json FROM verdict_cache").fetchone()[0]
    assert "call you Sunday" not in row


def test_pinned_entries_never_expire_but_normal_ones_do(settings, store, search):
    p = Pipeline(settings, Counting(), search, store)
    a = p.analyze(SEED["m10"], "michiko", pin=True)
    b = p.analyze(SEED["m02"], "michiko")
    ks = {m: p.cache_key(r.message, store.get_profile("michiko")) for m, r in (("m10", a), ("m02", b))}
    assert store.verdict_cache_get(ks["m10"], 0) is not None
    assert store.verdict_cache_get(ks["m02"], 0) is None and store.verdict_cache_get(ks["m02"], 3600) is not None


def test_seed_verdicts_are_free_after_warm(tmp_path, monkeypatch):
    from scamshield.cli import main

    db = tmp_path / "warm.db"
    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
    assert main(["warm", "--db", str(db)]) == 0
    settings = Settings.from_env({"SCAMSHIELD_OFFLINE": "1"})
    llm = Counting()
    p = Pipeline(settings, llm, FakeSearchClient([]), MemoryStore(str(db)))
    for mid, raw in load_seed_messages(settings.seed_dir):
        assert p.analyze(raw, "michiko").cached_from, mid
    assert llm.calls == 0


# --- rate limit ---------------------------------------------------------------------------------------
def test_rate_limiter_window_and_independent_clients():
    now = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: now[0])
    assert (rl.check("a"), rl.check("a")) == (0.0, 0.0)
    assert rl.check("a") == pytest.approx(60)
    assert rl.check("b") == 0.0
    now[0] = 61
    assert rl.check("a") == 0.0
    assert RateLimiter(0).check("a") == 0.0  # disabled


def test_client_ip_trusts_forwarded_header_only_when_told_to():
    assert client_ip("1.1.1.1", "9.9.9.9, 8.8.8.8", trust_forwarded=False) == "1.1.1.1"
    assert client_ip("1.1.1.1", "9.9.9.9, 8.8.8.8", trust_forwarded=True) == "8.8.8.8"
    assert client_ip(None, None, trust_forwarded=True) == "unknown"


def make_client(monkeypatch, **env):
    from scamshield import main

    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
    monkeypatch.setenv("SCAMSHIELD_DB", ":memory:")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()
    return TestClient(main.app), main


def test_analyze_endpoint_is_rate_limited_per_ip(monkeypatch):
    client, main = make_client(monkeypatch, RATE_LIMIT_PER_MINUTE="2")
    body = {"body": "Pay the fee in gift cards today", "sender": "212-555-0121"}
    codes = [client.post("/parents/p1/analyze", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    res = client.post("/parents/p1/analyze", json=body)
    assert res.status_code == 429 and int(res.headers["retry-after"]) >= 1
    assert client.get("/health").status_code == 200  # only analyze is limited
    other = client.post("/parents/p1/analyze", json=body, headers={"x-forwarded-for": "5.5.5.5"})
    assert other.status_code == 429  # header ignored unless TRUST_FORWARDED_FOR is set
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()


def test_budget_endpoint_and_trusted_forwarded_for(monkeypatch):
    client, main = make_client(monkeypatch, RATE_LIMIT_PER_MINUTE="1", TRUST_FORWARDED_FOR="1")
    body = {"body": "Pay the fee in gift cards today", "sender": "212-555-0121"}
    assert client.post("/parents/p1/analyze", json=body, headers={"x-forwarded-for": "5.5.5.5"}).status_code == 200
    assert client.post("/parents/p1/analyze", json=body, headers={"x-forwarded-for": "5.5.5.5"}).status_code == 429
    assert client.post("/parents/p1/analyze", json=body, headers={"x-forwarded-for": "6.6.6.6"}).status_code == 200
    b = client.get("/budget").json()
    assert b["mode"] == "normal" and b["total_usd"] == 0
    assert client.get("/health").json()["budget_mode"] == "normal"
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()


# --- Tavily extract -------------------------------------------------------------------------------------
def tavily_with(handler):
    return TavilyClient("k", transport=httpx.MockTransport(handler))


def test_tavily_search_is_basic_and_extract_is_one_url(store):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"title": "parcelpost-redelivery.example.com scam", "url": "https://news.example.org/a", "content": "short scam note"}]})
        return httpx.Response(200, json={"results": [{"url": "https://news.example.org/a", "raw_content": "full   page text " * 100}]})

    settings = Settings.from_env({"SCAMSHIELD_OFFLINE": "1"})
    r = Pipeline(settings, FakeLLMClient(), tavily_with(handler), store).analyze(SEED["m02"], "michiko")
    searches = [b for p, b in seen if p == "/search"]
    extracts = [b for p, b in seen if p == "/extract"]
    assert searches and all(b["search_depth"] == "basic" for b in searches)
    assert len(extracts) == 1 and extracts[0]["urls"] == ["https://news.example.org/a"]
    hit = r.lookups[0].hits[0]
    assert hit.excerpt.startswith("full page text") and len(hit.excerpt) <= 1500
    assert any(s.name == "tavily:extract" and s.cost_usd == pytest.approx(0.0016) for s in r.trace.steps)


def test_extract_is_capped_at_one_and_never_reads_the_messages_own_link(settings, store):
    class Spy(FakeSearchClient):
        pass

    fx = [
        {"match": "parcelpost-redelivery.example.com", "results": [
            {"title": "parcelpost-redelivery.example.com", "url": "http://parcelpost-redelivery.example.com/pay", "content": "scam parcelpost-redelivery.example.com"},
            {"title": "parcelpost-redelivery.example.com report", "url": "https://r.example.org/1", "content": "parcelpost-redelivery.example.com " + "x" * 10},
            {"title": "parcelpost-redelivery.example.com report 2", "url": "https://r.example.org/2", "content": "parcelpost-redelivery.example.com"}]},
    ]
    search = Spy(fx)
    Pipeline(settings, FakeLLMClient(), search, store).analyze(SEED["m02"], "michiko")
    assert len(search.extract_calls) <= 1
    assert "http://parcelpost-redelivery.example.com/pay" not in search.extract_calls


def test_extract_cache_and_failure_are_harmless(settings, store):
    def handler(request):
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"title": "parcelpost-redelivery.example.com scam", "url": "https://news.example.org/a", "content": "scam"}]})
        return httpx.Response(500)

    r = Pipeline(settings, FakeLLMClient(), tavily_with(handler), store).analyze(SEED["m02"], "michiko")
    assert r.verdict.label == "red"
    assert any(s.name == "tavily:extract" and s.error for s in r.trace.steps)
