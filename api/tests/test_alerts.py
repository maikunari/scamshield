"""Family alerts: Telegram + Resend over mocked HTTP, the unconfigured (recorded) path, dedupe, rate limit,
linking and the "I called her" acknowledgement."""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from scamshield import main
from scamshield.alerts import AlertService, build_content
from scamshield.config import Settings
from scamshield.llm import FakeLLMClient
from scamshield.memory import MemoryStore
from scamshield.models import RawMessage
from scamshield.pipeline import Pipeline
from scamshield.runtime import load_profile
from scamshield.search import FakeSearchClient

SCAM = "Grandma it's me, I had a car accident and need $3,000 for bail today. Please don't tell Mom. Buy gift cards and send me the codes."
TOKEN = "123456:SECRET-BOT-TOKEN"
RESEND_KEY = "re_SECRET_KEY"
HOOK_SECRET = "hook-secret"
BASE = "https://scamshield.example.org"

ENV = {
    "TELEGRAM_BOT_TOKEN": TOKEN,
    "TELEGRAM_WEBHOOK_SECRET": HOOK_SECRET,
    "RESEND_API_KEY": RESEND_KEY,
    "ALERT_EMAIL_FROM": "ScamShield <alerts@scamshield.example.org>",
    "PUBLIC_BASE_URL": BASE,
}


class FakeProviders:
    """Mocked Telegram + Resend. Records every request; ``fail`` makes a host answer 500."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.fail: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host in self.fail:
            return httpx.Response(500, json={"ok": False})
        if request.url.host == "api.resend.com":
            return httpx.Response(200, json={"id": "email_1"})
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "ScamShieldBot"}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def telegram(self, method: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.host == "api.telegram.org" and r.url.path.endswith("/" + method)]

    def alerts_sent(self, chat_id: str) -> list[dict]:
        """Alert messages (not the link confirmation) sent to a chat: they carry the inline keyboard."""
        return [m for m in self.telegram("sendMessage") if m["chat_id"] == chat_id and "reply_markup" in m]

    @property
    def emails(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "api.resend.com"]


@pytest.fixture
def providers():
    return FakeProviders()


def make_world(env: dict, providers: FakeProviders | None = None):
    settings = Settings.from_env({"SCAMSHIELD_OFFLINE": "", **env})
    # Offline LLM/search fakes, real alert config: build the pieces by hand instead of via the env switch.
    seed = Settings.from_env({"SCAMSHIELD_OFFLINE": "1"}).seed_dir
    profile = load_profile(seed)
    store = MemoryStore(":memory:")
    store.save_profile(profile)
    pipeline = Pipeline(settings, FakeLLMClient(), FakeSearchClient.from_file(seed / "search_fixtures.json"), store)
    service = AlertService(settings, store, providers.client() if providers else httpx.Client())
    return pipeline, service, profile, store


def red_result(pipeline, body=SCAM, sender="212-555-0142"):
    result = pipeline.analyze(RawMessage(body=body, sender=sender), "michiko")
    assert result.verdict.label == "red"
    return result


def link_kenji(service, store, profile, chat_id="777"):
    link = service.create_telegram_link(profile, "Kenji Hayashi")
    service.handle_update({"message": {"chat": {"id": int(chat_id)}, "text": f"/start {link['code']}"}})
    return link


# --- both channels configured -------------------------------------------------------------------------


def test_red_alert_goes_to_telegram_and_email(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    link_kenji(service, store, profile)
    result = red_result(pipeline)

    outcome = service.dispatch(result, profile)

    assert outcome.created and outcome.notified
    # Telegram: to the linked chat, HTML, with the inline "I called her" button carrying the alert id.
    sent = providers.alerts_sent("777")
    assert len(sent) == 1
    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    assert "Michiko" in msg["text"] and "Suggested words for the call" in msg["text"]
    assert f"{BASE}/results/{result.id}" in msg["text"]
    buttons = msg["reply_markup"]["inline_keyboard"]
    assert buttons[0][0] == {"text": "I called her", "callback_data": f"ack:{outcome.alert_id}"}
    assert buttons[1][0]["url"] == f"{BASE}/results/{result.id}"
    # Resend: one call, bearer auth, to Kenji's address, HTML and plain text with the ack link.
    [email] = providers.emails
    assert email.headers["authorization"] == f"Bearer {RESEND_KEY}"
    assert email.headers["idempotency-key"].startswith(f"alert-{outcome.alert_id}")
    body = json.loads(email.content)
    assert body["to"] == ["kenji.hayashi@family.example.com"]
    assert body["from"] == ENV["ALERT_EMAIL_FROM"]
    assert "I called her" in body["html"] and "tap here" in body["text"]
    alert = store.alert_get(outcome.alert_id)
    assert f"{BASE}/api/alerts/{outcome.alert_id}/ack?t={alert['token']}" in body["text"]
    assert f"{BASE}/api/alerts/{outcome.alert_id}/ack?t={alert['token']}" in body["html"]
    for flag in result.verdict.red_flags[:3]:
        assert flag.text in body["text"]
        if flag.evidence_url:
            assert flag.evidence_url in body["text"] and flag.evidence_url in body["html"]
    statuses = {(d["channel"], d["status"]) for d in alert["deliveries"]}
    assert statuses == {("telegram", "sent"), ("email", "sent")}


def test_only_the_adult_child_is_alerted_not_the_grandson(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    outcome = service.dispatch(red_result(pipeline), profile)
    contacts = {d["contact"] for d in store.alert_get(outcome.alert_id)["deliveries"]}
    assert contacts == {"Kenji Hayashi"}


def test_green_and_yellow_are_not_alerted(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    green = pipeline.analyze(RawMessage(body="Grandma it's Taro! The soccer game moved to Saturday at 2pm.", sender="212-555-0102"), "michiko")
    yellow = pipeline.analyze(
        RawMessage(body="Your order from Maple Garden Shop is out for delivery. Track: https://track-mapleparcel.example.com/8841", sender="212-555-0175"),
        "michiko",
    )
    assert (green.verdict.label, yellow.verdict.label) == ("green", "yellow")
    for r in (green, yellow):
        assert service.dispatch(r, profile).alert_id is None
    assert providers.requests == [] and store.alerts_for_parent("michiko") == []


def test_one_alert_per_message_even_when_checked_twice(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    link_kenji(service, store, profile)
    first = service.dispatch(red_result(pipeline), profile)
    again = service.dispatch(red_result(pipeline), profile)  # same text, same sender: a second check
    assert first.created and not again.created
    assert again.alert_id == first.alert_id and again.notified
    assert len(providers.emails) == 1
    assert len(providers.alerts_sent("777")) == 1
    assert len(store.alerts_for_parent("michiko")) == 1


def test_channel_failure_is_recorded_and_the_other_channel_still_goes(providers, caplog):
    pipeline, service, profile, store = make_world(ENV, providers)
    link_kenji(service, store, profile)
    providers.fail.add("api.telegram.org")
    with caplog.at_level(logging.DEBUG):
        outcome = service.dispatch(red_result(pipeline), profile)
    deliveries = {d["channel"]: d for d in store.alert_get(outcome.alert_id)["deliveries"]}
    assert deliveries["telegram"]["status"] == "failed" and deliveries["email"]["status"] == "sent"
    assert outcome.notified  # the email went out
    # No credential reaches the stored detail or the logs, whatever failed.
    assert TOKEN not in json.dumps(deliveries) and TOKEN not in caplog.text and RESEND_KEY not in caplog.text


def test_unlinked_telegram_is_skipped_when_the_bot_is_configured(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    outcome = service.dispatch(red_result(pipeline), profile)
    tg = [d for d in store.alert_get(outcome.alert_id)["deliveries"] if d["channel"] == "telegram"]
    assert [d["status"] for d in tg] == ["skipped"]
    assert len(providers.emails) == 1


# --- unconfigured: recorded, never failing -------------------------------------------------------------


def test_unconfigured_channels_record_the_alert_instead_of_sending(providers):
    pipeline, service, profile, store = make_world({"PUBLIC_BASE_URL": BASE}, providers)
    outcome = service.dispatch(red_result(pipeline), profile)

    assert outcome.created and not outcome.notified  # nothing actually went out
    assert providers.requests == []
    deliveries = {d["channel"]: d for d in store.alert_get(outcome.alert_id)["deliveries"]}
    assert {d["status"] for d in deliveries.values()} == {"recorded"}
    assert deliveries["email"]["target"] == "kenji.hayashi@family.example.com"
    assert deliveries["email"]["payload"]["subject"].startswith("ScamShield:")
    assert "Suggested words for the call" in deliveries["email"]["payload"]["text"]
    assert deliveries["telegram"]["payload"]["buttons"][0][0]["text"] == "I called her"


def test_email_needs_both_key_and_sender_address(providers):
    pipeline, service, profile, store = make_world({"RESEND_API_KEY": RESEND_KEY}, providers)
    outcome = service.dispatch(red_result(pipeline), profile)
    email = next(d for d in store.alert_get(outcome.alert_id)["deliveries"] if d["channel"] == "email")
    assert email["status"] == "recorded" and providers.requests == []


# --- rate limit ----------------------------------------------------------------------------------------


def test_alerts_per_contact_are_rate_limited(providers):
    env = {**ENV, "ALERT_MAX_PER_CONTACT_PER_HOUR": "2"}
    pipeline, service, profile, store = make_world(env, providers)
    link_kenji(service, store, profile)
    outcomes = [
        service.dispatch(red_result(pipeline, body=f"{SCAM} Case {i}."), profile) for i in range(4)
    ]
    assert len(providers.emails) == 2
    assert len(providers.alerts_sent("777")) == 2
    limited = store.alert_get(outcomes[3].alert_id)["deliveries"]
    assert [(d["channel"], d["status"]) for d in limited] == [("all", "rate_limited")]
    assert not outcomes[3].notified


# --- telegram linking and the webhook ------------------------------------------------------------------


def test_start_link_stores_the_chat_and_confirms(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    link = service.create_telegram_link(profile, "kenji hayashi")
    assert link["url"] == f"https://t.me/ScamShieldBot?start={link['code']}"
    service.handle_update({"message": {"chat": {"id": 4242}, "text": f"/start {link['code']}"}})
    assert store.telegram_chat("michiko", "Kenji Hayashi") == "4242"
    confirm = providers.telegram("sendMessage")[-1]
    assert confirm["chat_id"] == "4242" and "linked" in confirm["text"]
    # The code is single use.
    service.handle_update({"message": {"chat": {"id": 9999}, "text": f"/start {link['code']}"}})
    assert store.telegram_chat("michiko", "Kenji Hayashi") == "4242"
    assert "expired" in providers.telegram("sendMessage")[-1]["text"]


def test_only_family_contacts_get_a_link(providers):
    _, service, profile, _ = make_world(ENV, providers)
    assert service.create_telegram_link(profile, "Taro Hayashi") is None
    assert service.create_telegram_link(profile, "Nobody") is None


def test_register_webhook_needs_token_secret_and_https_url(providers):
    _, service, *_ = make_world(ENV, providers)
    assert service.register_webhook() is True
    [call] = providers.telegram("setWebhook")
    assert call["url"] == f"{BASE}/api/telegram/webhook" and call["secret_token"] == HOOK_SECRET
    for env in ({}, {**ENV, "PUBLIC_BASE_URL": "http://localhost:8000"}, {**ENV, "TELEGRAM_WEBHOOK_SECRET": ""}, {"PUBLIC_BASE_URL": BASE}):
        p = FakeProviders()
        _, svc, *_ = make_world(env, p)
        assert svc.register_webhook() is False and p.requests == []


# --- "I called her" ------------------------------------------------------------------------------------


def test_telegram_button_acknowledges_and_replaces_the_button(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    link_kenji(service, store, profile)
    outcome = service.dispatch(red_result(pipeline), profile)
    assert store.alert_get(outcome.alert_id)["acked_at"] is None

    service.handle_update({
        "callback_query": {
            "id": "cb1", "data": f"ack:{outcome.alert_id}",
            "message": {"message_id": 55, "chat": {"id": 777}},
        }
    })

    alert = store.alert_get(outcome.alert_id)
    assert alert["acked_at"] and alert["acked_by"] == "Kenji Hayashi" and alert["acked_via"] == "telegram"
    assert providers.telegram("answerCallbackQuery")[-1]["callback_query_id"] == "cb1"
    edit = providers.telegram("editMessageReplyMarkup")[-1]
    assert edit["chat_id"] == "777" and edit["message_id"] == 55


def test_a_stranger_cannot_acknowledge_through_telegram(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    outcome = service.dispatch(red_result(pipeline), profile)
    service.handle_update({
        "callback_query": {"id": "cb2", "data": f"ack:{outcome.alert_id}", "message": {"message_id": 1, "chat": {"id": 31337}}}
    })
    assert store.alert_get(outcome.alert_id)["acked_at"] is None


def test_email_link_acknowledges_only_with_the_right_token(providers):
    pipeline, service, profile, store = make_world(ENV, providers)
    outcome = service.dispatch(red_result(pipeline), profile)
    token = store.alert_get(outcome.alert_id)["token"]
    assert service.acknowledge_with_token(outcome.alert_id, "wrong") is None
    assert store.alert_get(outcome.alert_id)["acked_at"] is None
    assert service.acknowledge_with_token(outcome.alert_id, token) is True
    assert service.acknowledge_with_token(outcome.alert_id, token) is False  # already acknowledged
    assert store.alert_get(outcome.alert_id)["acked_via"] == "email"


# --- content safety ------------------------------------------------------------------------------------


def test_alert_text_escapes_scammer_controlled_strings_and_drops_non_http_links(profile, pipeline):
    result = red_result(pipeline)
    result.verdict.red_flags[0].text = '<script>alert(1)</script> & "quotes"'
    result.verdict.red_flags[0].evidence_url = "javascript:alert(1)"
    result.message.sender = "<b>evil</b>"
    c = build_content(result, profile.name, "a1", "tok", BASE)
    for rendered in (c.telegram, c.email_html):
        assert "<script>" not in rendered and "<b>evil</b>" not in rendered
        assert "javascript:" not in rendered
    assert "&lt;script&gt;" in c.email_html


# --- through the HTTP API ------------------------------------------------------------------------------


REAL_GET_ALERTS = main.get_alerts


def reset_caches():
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()
    REAL_GET_ALERTS.cache_clear()


@pytest.fixture
def api(monkeypatch, tmp_path, providers):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "")  # alert channels live; the API below still uses offline fakes
    monkeypatch.setenv("SCAMSHIELD_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    reset_caches()
    pipeline = main.get_pipeline()
    service = AlertService(pipeline.settings, pipeline.store, providers.client())
    monkeypatch.setattr(main, "get_alerts", lambda: service)
    with TestClient(main.app) as c:
        yield c, service, providers
    reset_caches()


def hook(c, update, secret=HOOK_SECRET):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
    return c.post("/api/telegram/webhook", json=update, headers=headers)


def test_api_end_to_end_link_check_alert_and_ack(api):
    c, service, providers = api
    link = c.post("/parents/michiko/telegram-link", json={"contact": "Kenji Hayashi"}).json()
    assert link["url"].startswith("https://t.me/ScamShieldBot?start=")
    assert hook(c, {"message": {"chat": {"id": 777}, "text": f"/start {link['code']}"}}).status_code == 200

    r = c.post("/api/check", json={"sender": "212-555-0142", "body": SCAM})
    assert r.status_code == 200
    assert r.json()["label"] == "red" and r.json()["family"] == {"name": "Kenji", "notified": True}

    [alert] = c.get("/parents/michiko/alerts").json()
    assert "token" not in alert and alert["acked_at"] is None
    assert {(d["channel"], d["status"]) for d in alert["deliveries"]} == {("telegram", "sent"), ("email", "sent")}

    # Email link: GET only confirms (a scanner prefetching the link must not acknowledge), POST acknowledges.
    token = service.store.alert_get(alert["id"])["token"]
    page = c.get(f"/api/alerts/{alert['id']}/ack", params={"t": token})
    assert page.status_code == 200 and "<form" in page.text
    assert c.get("/parents/michiko/alerts").json()[0]["acked_at"] is None
    done = c.post(f"/api/alerts/{alert['id']}/ack", params={"t": token})
    assert done.status_code == 200 and "Marked as called" in done.text
    acked = c.get("/parents/michiko/alerts").json()[0]
    assert acked["acked_at"] and acked["acked_via"] == "email"
    assert c.post(f"/api/alerts/{alert['id']}/ack", params={"t": "nope"}).status_code == 404
    assert c.get(f"/api/alerts/{alert['id']}/ack", params={"t": "nope"}).status_code == 404

    # Re-checking the same message neither re-alerts nor un-acknowledges.
    again = c.post("/api/check", json={"sender": "212-555-0142", "body": SCAM})
    assert again.json()["family"]["notified"] is True
    assert len(c.get("/parents/michiko/alerts").json()) == 1 and len(providers.emails) == 1


def test_webhook_rejects_a_missing_or_wrong_secret(api):
    c, *_ = api
    assert hook(c, {"message": {}}, secret=None).status_code == 403
    assert hook(c, {"message": {}}, secret="wrong").status_code == 403


def test_webhook_is_off_without_a_bot_token(monkeypatch, tmp_path):
    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
    monkeypatch.setenv("SCAMSHIELD_DB", str(tmp_path / "t.db"))
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    reset_caches()
    with TestClient(main.app) as c:
        assert hook(c, {"message": {}}).status_code == 404
        # Offline, a red check still works: the alert is recorded and the parent is not told it was delivered.
        r = c.post("/api/check", json={"sender": "212-555-0142", "body": SCAM})
        assert r.json()["label"] == "red" and r.json()["family"]["notified"] is False
        [alert] = c.get("/parents/michiko/alerts").json()
        assert {d["status"] for d in alert["deliveries"]} == {"recorded"}
    reset_caches()
