from scamshield.memory import MemoryStore, compare_to_contacts, service_owns_all_links
from scamshield.models import Message, RawMessage
from scamshield.normalize import normalize


def msg(sender, body="hello", **kw):
    return normalize(RawMessage(body=body, sender=sender, **kw))


def test_trusted_contact_by_phone_and_email(profile):
    assert compare_to_contacts(profile, msg("212-555-0102")).name == "Taro Hayashi"
    assert compare_to_contacts(profile, msg("Kenji <kenji.hayashi@family.example.com>")).status == "trusted"
    assert compare_to_contacts(profile, msg("090-0000-0101")).name == "Kenji Hayashi"


def test_unknown_and_lookalike_senders(profile):
    assert compare_to_contacts(profile, msg("212-555-0173")).status == "unknown"
    assert compare_to_contacts(profile, msg("statements@sakurabank-online.test")).status == "unknown"
    assert compare_to_contacts(profile, msg(None)).status == "unknown"


def test_known_service_must_own_the_links(profile):
    ok = msg("no-reply@sakurabank.example.com", "see https://www.sakurabank.example.com/x")
    bad = msg("no-reply@sakurabank.example.com", "see https://sakurabank-verify.test/x")
    m = compare_to_contacts(profile, ok)
    assert m.status == "known_service"
    assert service_owns_all_links(profile, m, ok)
    assert not service_owns_all_links(profile, compare_to_contacts(profile, bad), bad)


def test_green_body_dropped_unless_opted_in(pipeline, store):
    from scamshield.models import RawMessage

    r = pipeline.analyze(RawMessage(body="Mom, I'll call you Sunday.", sender="212-555-0101"), "michiko")
    assert r.verdict.label == "green"
    stored = store.get_analysis(r.id)
    assert stored.message.body == "" and stored.verdict.label == "green"
    store.save_analysis(r, keep_body=True)
    assert store.get_analysis(r.id).message.body.startswith("Mom")


def test_red_body_is_kept(pipeline, store):
    r = pipeline.analyze(RawMessage(body="Pay the fee in gift cards today, don't tell anyone", sender="212-555-0199"), "michiko")
    assert r.verdict.label == "red"
    assert "gift cards" in store.get_analysis(r.id).message.body


def test_search_cache_roundtrip_and_ttl(store):
    store.cache_put("k", [{"url": "u"}])
    assert store.cache_get("k", 60) == [{"url": "u"}]
    assert store.cache_get("k", -1) is None
    assert store.cache_get("missing", 60) is None
