from scamshield.models import Trace
from scamshield.planning import validate_plan
from scamshield.search import FakeSearchClient
from scamshield.skills import EvidenceDesk, check_phone, check_phrase, check_url

FIXTURES = [
    {"match": "212-555-0133", "results": [
        {"title": "reports", "url": "https://r.example.com/phone", "content": "Number 212-555-0133 reported for fraud"},
        {"title": "unrelated", "url": "https://r.example.com/other", "content": "A page about gardening"},
    ]},
    {"match": "evil.example.com", "results": [{"title": "evil.example.com phishing", "url": "https://r.example.com/dom", "content": "x"}]},
]


def make_desk(store, settings, fixtures=FIXTURES):
    client = FakeSearchClient(fixtures)
    return EvidenceDesk(client, store, settings, Trace()), client


def test_check_phone_keeps_only_relevant_hits(store, settings):
    desk, _ = make_desk(store, settings)
    lk = check_phone(desk, "+12125550133")
    assert [h.url for h in lk.hits] == ["https://r.example.com/phone"]
    assert lk.raw_hit_count == 2
    assert lk.query.startswith('"+12125550133" OR "212-555-0133"')


def test_nothing_found_means_no_hits_not_invented_evidence(store, settings):
    desk, _ = make_desk(store, settings)
    lk = check_phone(desk, "+12125550199")
    assert lk.hits == [] and lk.raw_hit_count == 0


def test_cache_serves_second_lookup_without_live_call(store, settings):
    desk, client = make_desk(store, settings)
    check_url(desk, "http://evil.example.com/pay")
    again = check_url(desk, "http://evil.example.com/pay")
    assert len(client.calls) == 1 and again.cached and again.hits
    assert desk.live_calls == 1


def test_budget_caps_live_calls_at_three(store, settings):
    desk, client = make_desk(store, settings)
    results = [check_phone(desk, f"+121255501{i:02d}") for i in range(5)]
    assert len(client.calls) == 3
    assert [r.skipped for r in results[3:]] == ["search budget exhausted"] * 2


def test_phrase_search_uses_news_topic_and_needs_scam_wording(store, settings):
    fx = [{"match": "your grandson", "results": [
        {"title": "Scam alert", "url": "https://n.example.com/1", "content": "grandparent scam"},
        {"title": "Cooking", "url": "https://n.example.com/2", "content": "pasta"}]}]
    desk, _ = make_desk(store, settings, fx)
    lk = check_phrase(desk, "your grandson needs bail")
    assert lk.topic == "news" and [h.url for h in lk.hits] == ["https://n.example.com/1"]


def test_every_search_is_traced(store, settings):
    desk, _ = make_desk(store, settings)
    check_phone(desk, "+12125550133")
    step = desk.trace.steps[0]
    assert step.name == "tavily:check_phone" and step.kind == "tool"


def test_validate_plan_only_accepts_message_values():
    cands = {"urls": ["http://a.test/x"], "phones": ["+12125550133"]}
    body = "Call 212-555-0133 now about http://a.test/x you won a prize"
    plan = {"lookups": [
        {"skill": "check_url", "value": "http://not-in-message.test"},
        {"skill": "check_phone", "value": "212-555-0133"},
        {"skill": "check_phone", "value": "+12125550133"},
        {"skill": "check_phrase", "value": "you won a prize"},
        {"skill": "check_phrase", "value": "completely invented words here"},
        {"skill": "check_url", "value": "http://a.test/x"},
        {"skill": "shell", "value": "rm -rf"},
        "junk",
    ]}
    assert validate_plan(plan, cands, body, 3) == [
        {"skill": "check_phone", "value": "+12125550133"},
        {"skill": "check_phrase", "value": "you won a prize"},
        {"skill": "check_url", "value": "http://a.test/x"},
    ]
    assert validate_plan("garbage", cands, body, 3) == []
    assert len(validate_plan(plan, cands, body, 1)) == 1


def test_phrase_pii_is_redacted_before_search():
    cands = {"urls": [], "phones": []}
    body = "my password: hunter2 was stolen"
    out = validate_plan({"lookups": [{"skill": "check_phrase", "value": "password: hunter2 was stolen"}]}, cands, body, 3)
    assert "hunter2" not in out[0]["value"]
