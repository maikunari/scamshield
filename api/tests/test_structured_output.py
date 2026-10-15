import json

import httpx
import pytest

from scamshield.llm import LLMError, LLMRequest, TokenFactoryClient
from scamshield.llm.schemas import SCHEMAS, TRIAGE_SCHEMA, VERDICT_SCHEMA
from scamshield.llm.tokenfactory import parse_json_content, strip_reasoning

SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}


def reply(content, status=200, model="m", usage=(10, 5)):
    return httpx.Response(
        status,
        json={"model": model, "choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}},
    )


def client_for(handler):
    return TokenFactoryClient("k", "https://tf.example.com/v1", transport=httpx.MockTransport(handler), backoff=0)


def req(**kw):
    return LLMRequest(task="triage", model="nvidia/Some-Model", system="sys", user="usr", schema=SCHEMA, **kw)


def test_json_schema_response_format_and_schema_in_prompt():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return reply('{"a": 1}')

    r = client_for(handler).complete_json(req())
    rf = bodies[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == SCHEMA and rf["json_schema"]["strict"] is True
    assert "JSON Schema" in bodies[0]["messages"][0]["content"] and '"required": ["a"]' in bodies[0]["messages"][0]["content"]
    assert (r.data, r.mode, len(bodies)) == ({"a": 1}, "json_schema", 1)


def test_falls_back_to_json_object_when_schema_rejected_and_remembers_it():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": "response_format json_schema is not supported"})
        return reply('{"a": 2}')

    c = client_for(handler)
    first = c.complete_json(req())
    assert (first.data, first.mode, bodies) == ({"a": 2}, "json_object", ["json_schema", "json_object"])
    c.complete_json(req())  # same model, case-insensitively: no second json_schema attempt
    assert bodies[2:] == ["json_object"]
    bodies.clear()
    other = LLMRequest(task="triage", model="NVIDIA/SOME-MODEL", system="s", user="u", schema=SCHEMA)
    c.complete_json(other)
    assert bodies == ["json_object"]


def test_repair_call_when_both_modes_return_garbage():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["messages"][0]["content"][:20])
        if len(calls) <= 2:
            return reply("Sure! here you go: {a: 1,}")
        return reply('{"a": 1}', usage=(7, 3))

    r = client_for(handler).complete_json(req())
    assert r.mode == "repair" and r.data == {"a": 1}
    assert calls[2].startswith("You repair malformed")
    assert (r.tokens_in, r.tokens_out) == (10 + 10 + 7, 5 + 5 + 3)  # every attempt is paid for and counted


def test_missing_required_keys_trigger_fallback_then_failure():
    c = client_for(lambda r: reply('{"b": 1}'))
    with pytest.raises(LLMError, match="unusable"):
        c.complete_json(req())


def test_empty_content_from_a_reasoning_model_fails_cleanly():
    c = client_for(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": "hmm"}}]}))
    with pytest.raises(LLMError):
        c.complete_json(req())


def test_non_schema_http_errors_are_not_swallowed():
    with pytest.raises(LLMError, match="401"):
        client_for(lambda r: httpx.Response(401, text="bad key")).complete_json(req())


@pytest.mark.parametrize(
    "content",
    [
        '<think>let me think {"a": 99}</think>{"a": 1}',
        '<thinking>hmm</thinking>\n```json\n{"a": 1}\n```',
        'long reasoning with {"a": 5} inside</think>\n{"a": 1}',  # template emitted only the closing tag
        '<reasoning>x</reasoning><think>y</think>{"a": 1}',
        '{"a": 1}<think>trailing thoughts that never end',
        '<THINK>\nmulti\nline\n</THINK>{"a": 1}',
    ],
)
def test_reasoning_blocks_are_stripped_before_parsing(content):
    assert parse_json_content(content) == {"a": 1}


def test_strip_reasoning_leaves_plain_text_alone():
    assert strip_reasoning('{"a": "think about it"}') == '{"a": "think about it"}'


def test_probe_reports_without_falling_back():
    c = client_for(lambda r: httpx.Response(400, json={"error": "no"}))
    res = c.probe_json_schema("m", SCHEMA, "s", "u")
    assert not res.ok and "400" in res.detail
    ok = client_for(lambda r: reply('{"a": 1}')).probe_json_schema("m", SCHEMA, "s", "u")
    assert ok.ok and ok.tokens_in == 10


def test_schemas_are_strict_and_cover_every_pydantic_field():
    from scamshield.models import Signals, Triage

    assert set(TRIAGE_SCHEMA["required"]) == set(TRIAGE_SCHEMA["properties"]) == {"signals", "risk", "scam_type", "key_phrase", "language", "summary"} <= set(Triage.model_fields)
    sig = TRIAGE_SCHEMA["properties"]["signals"]
    assert set(sig["required"]) == set(sig["properties"]) == set(Signals.model_fields)
    assert set(VERDICT_SCHEMA["required"]) == {"score", "red_flags", "safe_action", "family_points"}
    assert set(SCHEMAS) == {"triage", "plan", "verdict"}
    for schema in SCHEMAS.values():
        assert schema["additionalProperties"] is False
