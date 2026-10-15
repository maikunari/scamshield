import json

import httpx
import pytest

from scamshield.config import Settings
from scamshield.llm import FakeLLMClient
from scamshield.memory import compare_to_contacts, lookalike_service
from scamshield.models import RawMessage
from scamshield.normalize import normalize
from scamshield.pipeline import Pipeline
from scamshield.runtime import load_seed_messages
from scamshield.smoke import SmokeUnavailable, format_checks, run_smoke
from scamshield.tools import TOOL_SPECS, ToolError, Toolbox

SEED = dict(load_seed_messages(Settings.from_env({}).seed_dir))


# --- look-alike domains (the m15 eval miss) ---------------------------------------------------------------
@pytest.mark.parametrize(
    "sender,body,expected",
    [
        ("statements@lakeviewbank-online.test", "Log in: https://lakeviewbank-online.test/statement", True),
        ("alerts@lakeview-bank-secure.test", "x", True),
        (None, "see https://lakeviewbank.example.com.evil.test/login", True),
        (None, "see https://secure-lakeviewbank.test", True),
        ("no-reply@lakeviewbank.example.com", "https://www.lakeviewbank.example.com/statements", False),
        ("info@reliefnow.example.org", "https://reliefnow.example.org/donate", False),
        ("212-555-0190", "https://gardenworld-deals.example.com", False),
        (None, "https://track-mapleparcel.example.com/8841", False),
    ],
)
def test_lookalike_detection(profile, sender, body, expected):
    msg = normalize(RawMessage(body=body, sender=sender))
    assert (lookalike_service(profile, msg) is not None) is expected


def test_lookalike_is_reported_on_the_contact_match(profile):
    m = compare_to_contacts(profile, normalize(RawMessage(body="x", sender="statements@lakeviewbank-online.test")))
    assert m.status == "unknown" and m.lookalike_of == "Lakeview Example Bank"
    assert "lakeviewbank-online.test imitates Lakeview Example Bank" in m.reason


def test_m15_is_now_red_whatever_the_model_says(settings, store, search):
    class Lenient(FakeLLMClient):
        def complete_json(self, request):
            r = super().complete_json(request)
            if request.task == "verdict":
                r.data["score"] = 5
            if request.task == "triage":
                r.data["risk"] = "none"
            return r

    r = Pipeline(settings, Lenient(), search, store).analyze(SEED["m15"], "margaret")
    assert r.verdict.label == "red" and r.verdict.score == 75
    assert r.verdict.red_flags[0].text.startswith("The address looks like Lakeview Example Bank's")
    assert any("imitates Lakeview Example Bank" in s.detail for s in r.trace.steps)


def test_real_service_message_still_takes_the_fast_path(pipeline):
    r = pipeline.analyze(SEED["m16"], "margaret")
    assert r.verdict.label == "green" and r.contact.lookalike_of is None


# --- tools: plain JSON in / JSON out ----------------------------------------------------------------------
def test_tool_specs_are_valid_mcp_shapes():
    names = {t["name"] for t in TOOL_SPECS}
    assert names == {"check_message", "check_phone", "check_url", "check_phrase", "compare_to_contacts"}
    for t in TOOL_SPECS:
        assert t["description"] and t["inputSchema"]["type"] == "object" and t["inputSchema"]["required"]
    json.dumps(TOOL_SPECS)


def test_tools_round_trip_through_json(pipeline):
    box = Toolbox(pipeline)
    out = box.call("check_message", json.loads(json.dumps({"body": SEED["m10"].body, "sender": SEED["m10"].sender, "channel": "sms"})))
    again = json.loads(json.dumps(out))
    assert again["verdict"]["label"] == "red" and again["trace"]["steps"]
    phone = json.loads(json.dumps(box.call("check_phone", {"phone": "+12125550133"})))
    assert phone["lookup"]["skill"] == "check_phone" and phone["trace"]["steps"][0]["name"] == "tavily:check_phone"
    url = box.call("check_url", {"url": "http://parcelpost-redelivery.example.com/pay"})
    assert url["lookup"]["hits"]
    assert box.call("check_phrase", {"phrase": "your grandson needs bail"})["lookup"]["topic"] == "news"
    c = box.call("compare_to_contacts", {"sender": "212-555-0102", "parent_id": "margaret"})
    assert c["status"] == "trusted" and c["name"] == "Tom Reed"
    json.dumps([url, c])


def test_each_tool_call_has_its_own_search_budget(pipeline):
    box = Toolbox(pipeline)
    for i in range(5):
        out = box.call("check_phone", {"phone": f"+121255501{i:02d}"})
        assert out["lookup"]["skipped"] is None


def test_tool_errors(pipeline):
    box = Toolbox(pipeline)
    with pytest.raises(ToolError):
        box.call("check_phone", {})
    with pytest.raises(ToolError):
        box.call("check_url", {"url": 5})
    with pytest.raises(ToolError, match="unknown tool"):
        box.call("rm_rf", {})


# --- smoke script logic with mocked HTTP --------------------------------------------------------------------
MODELS = ["nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "NVIDIA/Nemotron-3-Super-120B-A12B", "nvidia/Nemotron-3-Ultra-550b-a55b", "nvidia/Nemotron-3_5-Lightning"]

TRIAGE = {"signals": {"urgency": True, "money_request": True, "impersonation": "delivery", "secrecy_request": False,
                      "credential_request": False, "link_present": True, "phone_present": False},
          "risk": "high", "scam_type": "parcel_delivery", "key_phrase": "redelivery fee required", "language": "en", "summary": "s"}


def fake_token_factory(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": m} for m in MODELS]})
    body = json.loads(request.content)
    task = body["response_format"].get("json_schema", {}).get("name", "")
    if task.endswith("triage") or task.endswith("probe"):
        data = TRIAGE
    else:
        data = {"score": 95, "red_flags": [{"text": "Asks for a fee", "evidence_url": None}], "safe_action": "Do not pay.", "family_points": ["A fake fee request."]}
    return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": json.dumps(data)}}],
                                     "usage": {"prompt_tokens": 300, "completion_tokens": 120}})


def fake_tavily(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"results": [{"title": "parcelpost-redelivery.example.com scam", "url": "https://r.example.org/1", "content": "parcelpost-redelivery.example.com is a phishing site"}]})


def test_smoke_passes_against_mocked_services():
    settings = Settings.from_env({"NEBIUS_API_KEY": "k", "TAVILY_API_KEY": "t"})
    checks, result = run_smoke(settings, llm_transport=httpx.MockTransport(fake_token_factory), search_transport=httpx.MockTransport(fake_tavily))
    text = format_checks(checks)
    assert all(c.ok for c in checks), text
    assert "MODEL_MID listed  - NVIDIA/Nemotron-3-Super-120B-A12B" in text  # catalog casing differs, still found
    assert text.count("json_schema on") == 3
    assert result is not None and result.verdict.label == "red" and not result.offline and result.trace.total_cost_usd > 0


def test_smoke_fails_loudly_when_a_model_rejects_json_schema():
    def handler(request):
        if request.url.path.endswith("/chat/completions") and "Ultra" in json.loads(request.content)["model"]:
            body = json.loads(request.content)
            if body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["name"].endswith("probe"):
                return httpx.Response(400, json={"error": "unsupported"})
        return fake_token_factory(request)

    settings = Settings.from_env({"NEBIUS_API_KEY": "k", "TAVILY_API_KEY": "t"})
    checks, _ = run_smoke(settings, llm_transport=httpx.MockTransport(handler), search_transport=httpx.MockTransport(fake_tavily))
    failed = [c for c in checks if not c.ok]
    assert [c.name for c in failed] == ["json_schema on nvidia/Nemotron-3-Ultra-550b-a55b"]


def test_smoke_needs_both_keys_and_online_mode():
    with pytest.raises(SmokeUnavailable, match="TAVILY_API_KEY"):
        run_smoke(Settings.from_env({"NEBIUS_API_KEY": "k"}))
    with pytest.raises(SmokeUnavailable, match="NEBIUS_API_KEY and TAVILY_API_KEY"):
        run_smoke(Settings.from_env({}))
    with pytest.raises(SmokeUnavailable, match="OFFLINE"):
        run_smoke(Settings.from_env({"NEBIUS_API_KEY": "k", "TAVILY_API_KEY": "t", "SCAMSHIELD_OFFLINE": "1"}))
