"""The fast check: quick previews, progress events, triage and lookups at the same time, the streamed endpoint and
the reasoning switch."""

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from scamshield import main
from scamshield.config import Settings
from scamshield.llm import FakeLLMClient, LLMRequest
from scamshield.llm.router import ModelRouter
from scamshield.llm.tokenfactory import TokenFactoryClient
from scamshield.memory import compare_to_contacts
from scamshield.models import RawMessage, Trace
from scamshield.normalize import normalize
from scamshield.pipeline import Pipeline
from scamshield.quick import quick_check

# The sample messages the live timings were taken on (G1 safe, S1-S5 scams).
G1 = ("212-555-0102", "Grandma it's Tom! The soccer game moved to Saturday at 2pm. Can you come? Love you!")
SCAMS = {
    "S1": ("212-555-0142", "Grandma it's me, I'm in big trouble. I had a car accident and I'm at the police station. I need $3,000 for bail today. Please don't tell Mom or Dad, I'm so embarrassed. Can you buy gift cards and send me the codes?"),
    "S2": ("212-555-0175", "FINAL NOTICE: Our records show an outstanding toll balance of $6.99 on your vehicle. Pay today to avoid a $50 late fee and suspension of your driving privileges: https://road-toll-pay.example.com/settle"),
    "S3": ("212-555-0163", "Fraud alert from your bank: your card ending 4412 was used for a $1,249.00 purchase. If this was not you, call our security department now at 212-555-0163. Do not tell anyone about this call, it is part of an active investigation."),
    "S4": ("212-555-0196", "This is an urgent notice from the Social Security Administration. Your Social Security number has been suspended due to suspicious activity. Call 212-555-0196 immediately or a warrant will be issued for your arrest."),
    "S5": ("212-555-0187", "ParcelPost: Your package could not be delivered. A redelivery fee of $1.99 is required. Pay within 24 hours: http://parcelpost-redelivery.example.com/pay"),
}


def quick_for(profile, sender, body):
    message = normalize(RawMessage(sender=sender, body=body))
    return quick_check(message, compare_to_contacts(profile, message))


# --- quick previews ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("sid", sorted(SCAMS))
def test_known_scam_scripts_get_a_red_preview(profile, sid):
    q = quick_for(profile, *SCAMS[sid])
    assert q is not None and q.label == "red" and q.reason


def test_trusted_contact_gets_a_green_preview(profile):
    q = quick_for(profile, *G1)
    assert q is not None and q.label == "green" and "Tom Reed" in q.reason


@pytest.mark.parametrize(
    "body",
    [
        "Grandma it's Tom, can you send me $200? Don't tell Dad.",
        "Hi Grandma, what's the code for the garage?",
        "Look at this: https://photos.example.com/album",
    ],
)
def test_trusted_contact_asking_for_money_codes_or_with_a_link_gets_no_preview(profile, body):
    assert quick_for(profile, "212-555-0102", body) is None


def test_unknown_sender_never_gets_a_green_preview(profile):
    assert quick_for(profile, "212-555-0999", "Hi, the soccer game moved to Saturday at 2pm.") is None
    assert quick_for(profile, None, "Lunch on Sunday?") is None


def test_lookalike_address_gets_a_red_preview(profile):
    q = quick_for(profile, "alerts@lakeviewbank-online.test", "Please review your statement.")
    assert q is not None and q.label == "red" and "Lakeview" in q.reason


def test_the_full_check_still_decides_after_a_red_preview(pipeline):
    # The preview fires on the script, but the verdict comes from the full check and is never replaced by it.
    events = list(pipeline.analyze_steps(RawMessage(sender=SCAMS["S5"][0], body=SCAMS["S5"][1]), "margaret"))
    quick = [e for e in events if e["type"] == "quick"]
    assert quick and quick[0]["label"] == "red"
    result = events[-1]["result"]
    assert any(s.name == "verdict" for s in result.trace.steps), "the verdict model still ran"
    assert any(s.name == "quick_check" for s in result.trace.steps)


class CalmTriage(FakeLLMClient):
    """A triage step that misses the scam entirely."""

    def complete_json(self, request: LLMRequest):
        response = super().complete_json(request)
        if request.task == "triage":
            response.data = {**response.data, "risk": "none"}
        return response


def test_a_red_preview_blocks_the_no_risk_shortcut(settings, store, search):
    r = Pipeline(settings, CalmTriage(), search, store).analyze(RawMessage(sender=SCAMS["S4"][0], body=SCAMS["S4"][1]), "margaret")
    assert r.triage.risk == "none"
    assert not any(s.name == "fast_path" for s in r.trace.steps)
    assert any(s.name == "verdict" for s in r.trace.steps)


# --- progress events ---------------------------------------------------------------------------------------
def test_events_tell_the_story_in_order(pipeline):
    events = list(pipeline.analyze_steps(RawMessage(sender=SCAMS["S5"][0], body=SCAMS["S5"][1]), "margaret"))
    kinds = [e.get("step", e["type"]) for e in events]
    assert kinds[0] == "reading" and kinds[-1] == "result"
    assert kinds.index("quick") < kinds.index("searching") < kinds.index("found") < kinds.index("deciding")
    found = next(e for e in events if e.get("step") == "found")
    result = events[-1]["result"]
    assert found["count"] == len({h.url for lk in result.lookups for h in lk.hits})


def test_trusted_fast_path_skips_searching_and_deciding(pipeline):
    events = list(pipeline.analyze_steps(RawMessage(sender=G1[0], body=G1[1]), "margaret"))
    kinds = [e.get("step", e["type"]) for e in events]
    assert kinds == ["reading", "quick", "result"]
    assert events[-1]["result"].verdict.label == "green"


def test_a_cached_verdict_is_a_single_result_event(pipeline):
    raw = RawMessage(sender=SCAMS["S2"][0], body=SCAMS["S2"][1])
    pipeline.analyze(raw, "margaret")
    events = list(pipeline.analyze_steps(raw, "margaret"))
    assert [e["type"] for e in events] == ["result"] and events[0]["result"].cached_from


def test_analyze_matches_the_last_event(pipeline):
    raw = RawMessage(sender=SCAMS["S3"][0], body=SCAMS["S3"][1])
    a = pipeline.analyze(raw, "margaret", use_cache=False)
    b = list(pipeline.analyze_steps(raw, "margaret", use_cache=False))[-1]["result"]
    assert (a.verdict.label, a.verdict.score, len(a.lookups)) == (b.verdict.label, b.verdict.score, len(b.lookups))


# --- triage and lookups at the same time -------------------------------------------------------------------
class SlowTriage(FakeLLMClient):
    def __init__(self):
        self.triage_done = 0.0

    def complete_json(self, request: LLMRequest):
        if request.task == "triage":
            time.sleep(0.3)
            self.triage_done = time.perf_counter()
        return super().complete_json(request)


class RecordingSearch:
    name = "recording"
    offline = True

    def __init__(self, inner):
        self.inner = inner
        self.started: list[tuple[str, float]] = []
        self.lock = threading.Lock()

    def search(self, query, *, topic="general", max_results=5):
        with self.lock:
            self.started.append((query, time.perf_counter()))
        time.sleep(0.05)
        return self.inner.search(query, topic=topic, max_results=max_results)

    def extract(self, url, *, max_chars=1500):
        return self.inner.extract(url, max_chars=max_chars)


def test_link_and_number_lookups_start_before_triage_finishes(settings, store, search):
    body = "Your bank account has been locked. Verify your identity today at http://secure-acct-verify.example.com/login"
    llm, rec = SlowTriage(), RecordingSearch(search)
    r = Pipeline(settings, llm, rec, store).analyze(RawMessage(sender="212-555-0177", body=body), "margaret")
    early = [q for q, t in rec.started if t < llm.triage_done]
    late = [q for q, t in rec.started if t >= llm.triage_done]
    assert any("secure-acct-verify.example.com" in q for q in early) and any("555-0177" in q for q in early), rec.started
    assert late and all(q.startswith('"') and "555" not in q for q in late), "the phrase lookup waits for triage's phrase"
    assert len(rec.started) <= settings.max_search_calls and r.verdict.label != "green"


def test_a_known_scam_script_starts_its_phrase_lookup_at_once(settings, store, search):
    llm, rec = SlowTriage(), RecordingSearch(search)
    Pipeline(settings, llm, rec, store).analyze(RawMessage(sender=SCAMS["S5"][0], body=SCAMS["S5"][1]), "margaret")
    assert len(rec.started) == 3 and all(t < llm.triage_done for _, t in rec.started)
    assert any(q == '"redelivery fee" scam' for q, _ in rec.started)


class SlowPages(RecordingSearch):
    def extract(self, url, *, max_chars=1500):
        time.sleep(0.5)
        return "a long report page " * 200


def test_a_report_page_still_loading_after_the_lookups_is_left_out(settings, store, search, monkeypatch):
    from scamshield import pipeline as pl

    monkeypatch.setattr(pl, "EXTRACT_GRACE_SECONDS", 0.05)
    r = Pipeline(settings, FakeLLMClient(), SlowPages(search), store).analyze(RawMessage(sender=SCAMS["S5"][0], body=SCAMS["S5"][1]), "margaret")
    step = next(s for s in r.trace.steps if s.name == "tavily:extract")
    assert step.detail.startswith("not used") and not step.error
    assert not any(h.excerpt for lk in r.lookups for h in lk.hits)
    time.sleep(0.6)  # the page arriving late must not touch the stored trace
    assert [s.name for s in r.trace.steps].count("tavily:extract") == 1


def test_a_report_page_in_time_reaches_the_verdict(settings, store, search):
    r = Pipeline(settings, FakeLLMClient(), SlowPages(search), store).analyze(RawMessage(sender=SCAMS["S5"][0], body=SCAMS["S5"][1]), "margaret")
    assert sum(1 for lk in r.lookups for h in lk.hits if h.excerpt) == 1


def test_no_extract_on_pages_behind_a_login():
    from scamshield.models import Lookup, SearchHit
    from scamshield.pipeline import _page_to_read

    lk = Lookup(skill="check_phrase", value="x", query="q", topic="news", hits=[
        SearchHit(title="t", url="https://www.facebook.com/post/1", content="c"),
        SearchHit(title="t", url="https://m.facebook.com/post/2", content="c"),
        SearchHit(title="t", url="https://consumer.ftc.gov/alerts/1", content="c"),
    ])
    assert _page_to_read(lk, normalize(RawMessage(body="hi"))) == "https://consumer.ftc.gov/alerts/1"


def test_trusted_sender_starts_no_lookup_before_triage(settings, store, search):
    rec = RecordingSearch(search)
    Pipeline(settings, SlowTriage(), rec, store).analyze(RawMessage(sender=G1[0], body=G1[1]), "margaret")
    assert rec.started == []


def test_no_plan_model_call(pipeline):
    r = pipeline.analyze(RawMessage(sender=SCAMS["S2"][0], body=SCAMS["S2"][1]), "margaret")
    assert [s.name for s in r.trace.steps if s.kind == "model"] == ["triage", "verdict"]


# --- streamed endpoint -------------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
    monkeypatch.setenv("SCAMSHIELD_DB", str(tmp_path / "test.db"))
    _reset()
    with TestClient(main.app) as c:
        yield c
    _reset()


def _reset():
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()
    main.get_alerts.cache_clear()


def sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream_sends_progress_then_the_check_response(client):
    sender, body = SCAMS["S1"]
    r = client.post("/api/check/stream", json={"sender": sender, "body": body})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse(r.text)
    assert events[0] == {"type": "step", "step": "reading"}
    assert any(e["type"] == "quick" and e["label"] == "red" for e in events)
    final = events[-1]
    assert final["type"] == "result"
    plain = client.post("/api/check", json={"sender": sender, "body": body}).json()  # cached by now
    assert set(final["result"]) == set(plain)
    assert final["result"]["label"] == plain["label"] == "red"
    assert "trace" not in final["result"] and "family_points" not in final["result"]


def test_stream_validates_and_rate_limits_before_streaming(client, monkeypatch):
    assert client.post("/api/check/stream", json={"body": "   "}).status_code == 422
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    _reset()
    assert client.post("/api/check/stream", json={"body": "hello there"}).status_code == 200
    r = client.post("/api/check/stream", json={"body": "hello again"})
    assert r.status_code == 429 and "retry-after" in r.headers


def test_stream_reports_a_failure_as_an_error_event(client, monkeypatch):
    def boom(*args, **kwargs):
        yield {"type": "step", "step": "reading"}
        raise RuntimeError("down")

    monkeypatch.setattr(main.get_pipeline(), "analyze_steps", boom)
    events = sse(client.post("/api/check/stream", json={"body": "hello"}).text)
    assert events == [{"type": "step", "step": "reading"}, {"type": "error"}]


# --- reasoning switch --------------------------------------------------------------------------------------
def test_model_thinking_setting():
    assert Settings.from_env({}).thinking_tasks == frozenset()
    assert Settings.from_env({"MODEL_THINKING": "verdict"}).thinking_tasks == {"verdict"}
    assert Settings.from_env({"MODEL_THINKING": "Triage, verdict"}).thinking_tasks == {"triage", "verdict"}
    assert Settings.from_env({"MODEL_THINKING": "none"}).thinking_tasks == frozenset()


@pytest.mark.parametrize("thinking, sent", [("", False), ("verdict", True)])
def test_router_asks_token_factory_to_skip_reasoning_unless_configured(thinking, sent):
    bodies = []

    def handler(request: httpx.Request):
        bodies.append(json.loads(request.content))
        verdict = {"score": 90, "red_flags": [], "safe_action": "Do not pay.", "family_points": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(verdict)}}], "usage": {}})

    settings = Settings.from_env({"NEBIUS_API_KEY": "k", "MODEL_THINKING": thinking})
    client = TokenFactoryClient("k", settings.nebius_base_url, transport=httpx.MockTransport(handler))
    ModelRouter(client, settings).call("verdict", "s", "u", {}, Trace())
    if sent:
        assert "chat_template_kwargs" not in bodies[0]
    else:
        assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}
