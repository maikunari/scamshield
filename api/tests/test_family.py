"""The family gate, the dashboard's read endpoints, memory edits and the demo family (all offline)."""

import time

import pytest
from conftest import sign_in
from fastapi.testclient import TestClient

from scamshield import main
from scamshield.config import DEFAULT_FAMILY_PASSPHRASE

SCAM = "Grandma it's me, I had a car accident and need $3,000 for bail today. Please don't tell Mom. Buy gift cards and send me the codes."
GATED = [
    ("get", "/parents/margaret/profile"),
    ("put", "/parents/margaret/profile"),
    ("post", "/parents/margaret/analyze"),
    ("post", "/parents/margaret/telegram-link"),
    ("get", "/parents/margaret/alerts"),
    ("post", "/parents/margaret/alerts/x/ack"),
    ("get", "/parents/margaret/checks"),
    ("get", "/parents/margaret/family"),
    ("get", "/results/anything"),
    ("get", "/api/family/session"),
]


def reset():
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()
    main.get_alerts.cache_clear()


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    def build(**env):
        monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
        monkeypatch.setenv("SCAMSHIELD_DB", str(tmp_path / "family.db"))
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        reset()
        return TestClient(main.app)

    yield build
    reset()


@pytest.fixture
def client(make_client):
    return make_client()


def load_demo(client):
    r = client.post("/api/demo/load")
    assert r.status_code == 200, r.text
    return r.json()


# --- the gate ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", GATED)
def test_family_routes_need_a_session(client, method, path):
    r = getattr(client, method)(path, **({"json": {}} if method in ("put", "post") else {}))
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_parent_side_stays_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/family/gate").status_code == 200
    r = client.post("/api/check", json={"sender": "212-555-0102", "body": "Grandma it's Tom! Soccer moved to Saturday at 2pm."})
    assert r.status_code == 200 and r.json()["label"] == "green"
    # the call script and trace are not in the parent's response
    assert "family_script" not in r.text and "trace" not in r.text


def test_login_rejects_a_wrong_passphrase_and_accepts_the_right_one(client):
    bad = client.post("/api/family/login", json={"passphrase": "nope"})
    assert bad.status_code == 401 and "set-cookie" not in bad.headers
    assert client.post("/api/family/login", json={"passphrase": ""}).status_code == 401
    assert client.post("/api/family/login", json={"passphrase": "x", "parent_id": "nobody"}).status_code == 401

    ok = client.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE})
    assert ok.status_code == 200
    assert ok.json()["parent_id"] == "margaret" and ok.json()["name"] == "Margaret Reed"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    # the cookie alone now opens the dashboard routes
    assert client.get("/api/family/session").json()["parent_id"] == "margaret"
    assert client.get("/parents/margaret/checks").status_code == 200


def test_bearer_token_works_without_the_cookie(client):
    token = client.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE}).json()["token"]
    client.cookies.clear()
    assert client.get("/parents/margaret/checks").status_code == 401
    ok = client.get("/parents/margaret/checks", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    # the passphrase itself is not a bearer credential; it must be traded at /api/family/login
    assert client.get("/parents/margaret/checks", headers={"Authorization": f"Bearer {DEFAULT_FAMILY_PASSPHRASE}"}).status_code == 401


def test_logout_clears_the_session(client):
    client.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE})
    assert client.get("/api/family/session").status_code == 200
    client.post("/api/family/logout")
    assert client.get("/api/family/session").status_code == 401


def test_session_is_for_one_family_only(client):
    sign_in(client, "other-family", "their-passphrase")
    assert client.get("/parents/other-family/checks").status_code == 200
    assert client.get("/parents/margaret/checks").status_code == 401
    assert client.get("/parents/margaret/profile").status_code == 401


def test_forged_tampered_and_expired_sessions_are_refused(client, monkeypatch):
    token = client.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE}).json()["token"]
    client.cookies.clear()
    for bad in (token[:-2] + "00", "v1.bWljaGlrbw.9999999999.abc.def", "garbage", token.replace("v1.", "v2.", 1), ""):
        assert client.get("/parents/margaret/checks", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    # a payload for another parent signed with the same shape but the wrong key does not verify
    other = token.split(".")
    other[1] = "b3RoZXI"  # "other"
    assert client.get("/parents/other/checks", headers={"Authorization": "Bearer " + ".".join(other)}).status_code == 401
    # expiry
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 8 * 24 * 3600)
    assert client.get("/parents/margaret/checks", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_changing_the_passphrase_signs_everyone_out(client):
    token = client.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE}).json()["token"]
    main.get_auth().set_passphrase("margaret", "a brand new one")
    assert client.get("/parents/margaret/checks", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE}).status_code == 401
    assert client.post("/api/family/login", json={"passphrase": "a brand new one"}).status_code == 200


def test_configured_passphrase_wins_and_hides_the_demo_hint(make_client):
    c = make_client(FAMILY_PASSPHRASE="kitchen table 1952")
    assert c.get("/api/family/gate").json()["demo_passphrase"] is None
    assert c.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE}).status_code == 401
    assert c.post("/api/family/login", json={"passphrase": "kitchen table 1952"}).status_code == 200
    loaded = load_demo(c)
    assert loaded["passphrase"] is None  # a private passphrase is never echoed
    assert c.post("/api/family/login", json={"passphrase": "kitchen table 1952"}).status_code == 200


def test_passphrase_guessing_is_rate_limited(make_client):
    c = make_client(LOGIN_ATTEMPTS_PER_MINUTE="3")
    codes = [c.post("/api/family/login", json={"passphrase": f"guess{i}"}).status_code for i in range(4)]
    assert codes == [401, 401, 401, 429]


def test_session_secret_survives_a_restart(make_client, monkeypatch):
    c = make_client()
    token = c.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE}).json()["token"]
    reset()  # a new process on the same database
    c2 = TestClient(main.app)
    assert c2.get("/parents/margaret/checks", headers={"Authorization": f"Bearer {token}"}).status_code == 200


# --- the demo family --------------------------------------------------------------------------------


def test_load_demo_family_seeds_profile_checks_and_alerts(client):
    loaded = load_demo(client)
    assert loaded == {"parent_id": "margaret", "name": "Margaret Reed", "checks": 5, "alerts": 2, "passphrase": DEFAULT_FAMILY_PASSPHRASE}
    assert client.get("/api/family/gate").json() == {"demo_load": True, "demo_passphrase": DEFAULT_FAMILY_PASSPHRASE}
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)

    profile = client.get("/parents/margaret/profile").json()
    assert [c["name"] for c in profile["trusted_contacts"]] == ["David Reed", "Tom Reed"]

    checks = client.get("/parents/margaret/checks").json()
    assert [c["label"] for c in checks] == ["red", "yellow", "green", "red", "green"]  # newest first
    assert all(c["demo"] for c in checks)
    newest, acked = checks[0], checks[3]
    assert newest["alert"]["acked_at"] is None
    assert {d["channel"] for d in newest["alert"]["deliveries"]} == {"email", "telegram"}
    assert all(d["status"] == "recorded" for d in newest["alert"]["deliveries"])  # nothing was really sent
    assert acked["alert"]["acked_by"] == "David Reed" and acked["alert"]["acked_via"] == "telegram"
    assert checks[1]["alert"] is None  # yellow: no alert
    assert newest["trace"]["model_calls"] == 3 and len(newest["trace"]["models"]) == 3
    # safe messages are forgotten: hash and label only, because the family has not opted in to keeping them
    assert checks[2]["body_kept"] is False and checks[2]["snippet"] == ""
    assert checks[0]["body_kept"] is True and checks[0]["snippet"]

    full = client.get(f"/results/{newest['id']}").json()
    assert full["demo"] is True and full["verdict"]["family_script"]
    assert [s["name"] for s in full["trace"]["steps"] if s["kind"] == "model"] == ["triage", "plan", "verdict"]


def test_load_demo_twice_resets_instead_of_duplicating(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    client.post("/api/check", json={"sender": "212-555-0142", "body": SCAM})
    assert len(client.get("/parents/margaret/checks").json()) == 6
    load_demo(client)
    assert len(client.get("/parents/margaret/checks").json()) == 5
    assert len(client.get("/parents/margaret/alerts").json()) == 2


def test_judge_path_example_check_shows_up_with_alert_and_trace(client):
    """Load demo family -> check an example -> red -> /family shows the alert and the stored trace."""
    load_demo(client)
    r = client.post("/api/check", json={"sender": "212-555-0142", "body": SCAM})
    assert r.json()["label"] == "red"

    token = client.post("/api/family/login", json={"passphrase": DEFAULT_FAMILY_PASSPHRASE}).json()["token"]
    checks = client.get("/parents/margaret/checks", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(checks) == 6
    mine = checks[0]
    assert mine["id"] == r.json()["id"] and mine["label"] == "red" and not mine["demo"]
    assert mine["alert"] and mine["alert"]["acked_at"] is None
    assert mine["alert"]["id"] not in {c["alert"]["id"] for c in checks[1:] if c["alert"]}  # a new alert, not a seeded one

    trace = client.get(f"/results/{mine['id']}", headers={"Authorization": f"Bearer {token}"}).json()["trace"]["steps"]
    assert {"normalize", "triage", "memory", "verdict"} <= {s["name"] for s in trace}


def test_demo_load_can_be_switched_off(make_client):
    c = make_client(DEMO_LOAD_ENABLED="0")
    assert c.post("/api/demo/load").status_code == 404
    assert c.get("/api/family/gate").json() == {"demo_load": False, "demo_passphrase": None}


def test_demo_load_is_rate_limited(make_client):
    c = make_client(LOGIN_ATTEMPTS_PER_MINUTE="2")
    assert [c.post("/api/demo/load").status_code for _ in range(3)] == [200, 200, 429]


# --- acknowledgement and family info ----------------------------------------------------------------


def test_i_called_her_from_the_dashboard(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    alert_id = client.get("/parents/margaret/checks").json()[0]["alert"]["id"]
    first = client.post(f"/parents/margaret/alerts/{alert_id}/ack")
    assert first.status_code == 200 and first.json()["newly"] is True and "token" not in first.json()
    assert first.json()["acked_via"] == "dashboard" and first.json()["acked_at"]
    assert client.post(f"/parents/margaret/alerts/{alert_id}/ack").json()["newly"] is False
    assert client.get("/parents/margaret/checks").json()[0]["alert"]["acked_via"] == "dashboard"
    assert client.post("/parents/margaret/alerts/nope/ack").status_code == 404


def test_cannot_acknowledge_another_familys_alert(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    alert_id = client.get("/parents/margaret/checks").json()[0]["alert"]["id"]
    sign_in(client, "other-family", "x")
    assert client.post(f"/parents/other-family/alerts/{alert_id}/ack").status_code == 404
    assert client.get("/results/demo-bank").status_code == 404


def test_family_info_lists_alert_contacts_and_channels(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    info = client.get("/parents/margaret/family").json()
    assert [c["name"] for c in info["contacts"]] == ["David Reed"]  # the grandson is not an alert recipient
    assert info["contacts"][0]["telegram_linked"] is False and info["contacts"][0]["emails"]
    assert info["telegram_configured"] is False and info["email_configured"] is False
    link = client.post("/parents/margaret/telegram-link", json={"contact": "David Reed"}).json()
    assert link["bot_configured"] is False and link["code"] and link["url"] is None


# --- memory edits -----------------------------------------------------------------------------------


def test_trusted_contact_edit_changes_the_next_verdict(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    ask = {"sender": "212-555-0177", "body": "Grandma it's Yuto! I got a new phone. Can you send me $300 for a train ticket? Don't tell Mom."}
    before = client.post("/api/check", json=ask).json()
    assert before["contact"]["status"] == "unknown" and before["label"] != "green"

    profile = client.get("/parents/margaret/profile").json()
    profile["trusted_contacts"].append({"name": "  Yuto Reed ", "relationship": "Grandson", "phones": ["(212) 555-0177"], "emails": []})
    saved = client.put("/parents/margaret/profile", json=profile)
    assert saved.status_code == 200
    assert saved.json()["trusted_contacts"][-1] == {"name": "Yuto Reed", "relationship": "grandson", "phones": ["(212) 555-0177"], "emails": []}

    after = client.post("/api/check", json=ask).json()
    assert after["contact"]["status"] == "trusted" and after["contact"]["name"] == "Yuto Reed"
    assert after["score"] < before["score"]
    assert client.get("/parents/margaret/profile").json()["trusted_contacts"][-1]["name"] == "Yuto Reed"


def test_removing_a_contact_removes_the_trust(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    tom = {"sender": "212-555-0102", "body": "Grandma it's Tom! The soccer game moved to Saturday at 2pm. Can you come? Love you!"}
    assert client.post("/api/check", json=tom).json()["contact"]["status"] == "trusted"
    profile = client.get("/parents/margaret/profile").json()
    profile["trusted_contacts"] = [c for c in profile["trusted_contacts"] if c["name"] != "Tom Reed"]
    assert client.put("/parents/margaret/profile", json=profile).status_code == 200
    assert client.post("/api/check", json=tom).json()["contact"]["status"] == "unknown"


def test_known_service_edit_normalises_domains(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    profile = client.get("/parents/margaret/profile").json()
    profile["known_services"].append(
        {"name": "Reed Water", "kind": "Utility", "domains": ["https://www.Reed-Water.example.com/pay", "reed-water.example.com"], "phones": [], "senders": [" bills@reed-water.example.com "]}
    )
    saved = client.put("/parents/margaret/profile", json=profile).json()["known_services"][-1]
    assert saved["domains"] == ["reed-water.example.com"] and saved["kind"] == "utility"
    assert saved["senders"] == ["bills@reed-water.example.com"]


@pytest.mark.parametrize(
    "edit,fragment",
    [
        (lambda p: p["trusted_contacts"].append({"name": "Bad", "phones": ["not a number"]}), "phone number"),
        (lambda p: p["trusted_contacts"].append({"name": "Bad", "emails": ["nope"]}), "email address"),
        (lambda p: p["trusted_contacts"].append({"name": "  ", "phones": []}), "needs a name"),
        (lambda p: p["trusted_contacts"].append({"name": "david reed"}), "same name"),
        (lambda p: p["known_services"].append({"name": "Bad", "domains": ["not a domain"]}), "website address"),
        (lambda p: p.update(name=" "), "name can't be empty"),
        (lambda p: p["trusted_contacts"].append({"name": "Many", "phones": [f"212-555-01{i:02d}" for i in range(11)]}), "at most"),
    ],
)
def test_invalid_memory_edits_are_refused_and_change_nothing(client, edit, fragment):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    before = client.get("/parents/margaret/profile").json()
    profile = client.get("/parents/margaret/profile").json()
    edit(profile)
    r = client.put("/parents/margaret/profile", json=profile)
    assert r.status_code == 422 and fragment in r.json()["detail"]
    assert client.get("/parents/margaret/profile").json() == before


def test_profile_put_cannot_move_a_profile_to_another_family(client):
    load_demo(client)
    sign_in(client, "margaret", DEFAULT_FAMILY_PASSPHRASE)
    profile = client.get("/parents/margaret/profile").json()
    profile["id"] = "someone-else"
    assert client.put("/parents/margaret/profile", json=profile).json()["id"] == "margaret"
    assert client.get("/parents/someone-else/profile").status_code == 401


def test_alert_links_point_at_the_dashboard():
    from scamshield.alerts import build_content
    from scamshield.config import Settings
    from scamshield.llm import FakeLLMClient
    from scamshield.memory import MemoryStore
    from scamshield.models import RawMessage
    from scamshield.pipeline import Pipeline
    from scamshield.runtime import load_profile
    from scamshield.search import FakeSearchClient

    s = Settings.from_env({"SCAMSHIELD_OFFLINE": "1"})
    store = MemoryStore(":memory:")
    store.save_profile(load_profile(s.seed_dir))
    result = Pipeline(s, FakeLLMClient(), FakeSearchClient.from_file(s.seed_dir / "search_fixtures.json"), store).analyze(
        RawMessage(body=SCAM, sender="212-555-0142"), "margaret"
    )
    content = build_content(result, "Margaret Reed", "a1", "tok", "https://scam.example.org")
    assert content.check_url == f"https://scam.example.org/family/?check={result.id}"
