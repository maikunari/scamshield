import json

import httpx
import pytest

from scamshield.config import Settings, resolve_model_id
from scamshield.discovery import check_defaults, fetch_models, render_markdown
from scamshield.llm import LLMError, LLMRequest, TokenFactoryClient
from scamshield.llm.tokenfactory import parse_json_content
from scamshield.search import FakeSearchClient, SearchError, TavilyClient


def test_settings_offline_selection():
    assert Settings.from_env({}).llm_offline and Settings.from_env({}).search_offline
    live = Settings.from_env({"NEBIUS_API_KEY": "k", "TAVILY_API_KEY": "t"})
    assert not live.llm_offline and not live.search_offline
    assert Settings.from_env({"NEBIUS_API_KEY": "k", "TAVILY_API_KEY": "t", "SCAMSHIELD_OFFLINE": "1"}).llm_offline


def test_default_models_are_the_catalog_ids():
    live = Settings.from_env({"NEBIUS_API_KEY": "k"})
    assert [live.model_for(t) for t in ("fast", "mid", "reason", "long")] == [
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/Nemotron-3-Ultra-550b-a55b",
        "nvidia/Nemotron-3_5-Lightning",
    ]
    assert Settings.from_env({"NEBIUS_API_KEY": "k", "MODEL_FAST": "m"}).model_for("fast") == "m"
    assert Settings.from_env({}).model_for("fast") == "fake/fast"
    assert Settings.from_env({}).nebius_base_url == "https://api.tokenfactory.nebius.com/v1/"


def test_prices_follow_the_model_case_insensitively_and_env_overrides_win():
    s = Settings.from_env({})
    assert s.price_for("reason", "nvidia/Nemotron-3-Ultra-550b-a55b") == (1.00, 3.00)
    assert s.price_for("reason", "NVIDIA/NEMOTRON-3-ULTRA-550B-A55B") == (1.00, 3.00)
    assert s.price_for("mid", "nvidia/nemotron-3-super-120b-a12b") == (0.30, 0.90)
    assert s.price_for("fast", "nvidia/nvidia-nemotron-3-nano-30b-a3b") == (0.06, 0.24)
    assert s.price_for("long", "nvidia/Nemotron-3_5-Lightning") == (0.06, 0.24)
    assert s.price_for("mid", "fake/mid") == (0.30, 0.90)  # unknown id: tier default
    assert Settings.from_env({"PRICE_REASON_IN": "2"}).price_for("reason", "nvidia/Nemotron-3-Ultra-550b-a55b") == (2.0, 3.0)


def test_resolve_model_id_ignores_case():
    assert resolve_model_id("nvidia/nemotron-3-ultra-550b-a55b", ["x", "nvidia/Nemotron-3-Ultra-550b-a55b"]) == "nvidia/Nemotron-3-Ultra-550b-a55b"
    assert resolve_model_id("nvidia/missing", ["x"]) is None


def test_parse_json_content_tolerates_fences_and_think_blocks():
    assert parse_json_content('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_content('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(LLMError):
        parse_json_content("no json here")
    with pytest.raises(LLMError):
        parse_json_content("[1, 2]")


def completion(content, status=200):
    return httpx.Response(status, json={"model": "m-real", "choices": [{"message": {"content": content}}],
                                        "usage": {"prompt_tokens": 11, "completion_tokens": 7}})


def test_token_factory_request_and_usage():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return completion('{"ok": true}')

    c = TokenFactoryClient("sekret", "https://tf.example.com/v1/", transport=httpx.MockTransport(handler))
    r = c.complete_json(LLMRequest(task="triage", model="nano-1", system="s", user="u"))
    assert seen["url"] == "https://tf.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sekret"
    assert seen["body"]["model"] == "nano-1" and seen["body"]["response_format"] == {"type": "json_object"}  # no schema given
    assert (r.data, r.tokens_in, r.tokens_out, r.model) == ({"ok": True}, 11, 7, "m-real")


def test_token_factory_retries_then_fails():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else completion('{"ok": 1}')

    c = TokenFactoryClient("k", "https://tf.example.com/v1", transport=httpx.MockTransport(handler), backoff=0)
    assert c.complete_json(LLMRequest("plan", "m", "s", "u")).data == {"ok": 1}
    assert len(calls) == 3

    always = TokenFactoryClient("k", "https://tf.example.com/v1", transport=httpx.MockTransport(lambda r: httpx.Response(401)), backoff=0)
    with pytest.raises(LLMError):
        always.complete_json(LLMRequest("plan", "m", "s", "u"))


def test_tavily_client_only_talks_to_tavily_and_parses_results():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(host=request.url.host, path=request.url.path, auth=request.headers["authorization"], body=json.loads(request.content))
        return httpx.Response(200, json={"results": [{"title": "t", "url": "https://r.example.com/1", "content": "c"}, {"title": "no url"}]})

    c = TavilyClient("tkey", transport=httpx.MockTransport(handler))
    hits = c.search("q", topic="news")
    assert (seen["host"], seen["path"], seen["auth"]) == ("api.tavily.com", "/search", "Bearer tkey")
    assert seen["body"]["topic"] == "news" and seen["body"]["query"] == "q"
    assert [h.url for h in hits] == ["https://r.example.com/1"]

    bad = TavilyClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(SearchError):
        bad.search("q")


def test_fake_search_matches_ignoring_punctuation():
    fx = FakeSearchClient([{"match": "212-555-0133", "results": [{"title": "t", "url": "https://x.example.com", "content": "c"}]}])
    assert fx.search('"+12125550133" OR "(212) 555-0133" scam')
    assert fx.search("something else") == []


def test_discovery_renders_models_table():
    def handler(request):
        assert request.url.path == "/v1/models" and request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"data": [{"id": "nvidia/Nemotron-Nano-9B", "owned_by": "nvidia"}, {"id": "other/model"}]})

    models = fetch_models("https://tf.example.com/v1/", "k", transport=httpx.MockTransport(handler))
    md = render_markdown(models, "https://tf.example.com/v1/")
    assert "`nvidia/Nemotron-Nano-9B`" in md and "`MODEL_FAST` | Nemotron Nano | `nvidia/Nemotron-Nano-9B`" in md
    assert "`MODEL_REASON` | Nemotron Ultra | _none listed_" in md
    assert "MODEL_SAFETY" not in md and "`MODEL_LONG` | Nemotron 3.5 Lightning | _none listed_" in md
    assert "`MODEL_REASON` | `nvidia/Nemotron-3-Ultra-550b-a55b` | **not listed**" in md


def test_discovery_matches_default_ids_case_insensitively():
    listing = [{"id": "NVIDIA/nemotron-3-super-120B-a12b"}, {"id": "nvidia/Nemotron-3_5-Lightning"}]
    found = {env: listed for env, _, listed in check_defaults(listing)}
    assert found["MODEL_MID"] == "NVIDIA/nemotron-3-super-120B-a12b" and found["MODEL_FAST"] is None
