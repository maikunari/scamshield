"""Delete, export and retention, and the redaction guarantee they sit next to."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in
from scamshield import main
from scamshield.models import RawMessage
from scamshield.privacy import Retention, export_parent
from scamshield.redact import redact

SCAM = "Grandma it's me, I had a car accident and need $3,000 for bail today. Buy gift cards and send me the codes."
SAFE = "Your pharmacy order is ready for pickup."


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
    monkeypatch.setenv("SCAMSHIELD_DB", str(tmp_path / "test.db"))
    _reset()
    with TestClient(main.app) as c:
        sign_in(c)
        yield c
    _reset()


def _reset():
    main.get_pipeline.cache_clear()
    main.get_limiter.cache_clear()
    main.get_alerts.cache_clear()
    main.get_retention.cache_clear()


def _check(client, body=SCAM, sender="212-555-0142"):
    r = client.post("/api/check", json={"body": body, "sender": sender})
    assert r.status_code == 200, r.text
    return r.json()


def _rows(store, table):
    return store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_export_has_everything_stored_and_no_secrets(client):
    first = _check(client)
    data = client.get("/api/my-data/export").json()
    assert data["parent_id"] == "margaret"
    assert data["profile"]["name"]
    assert [c["id"] for c in data["checks"]] == [first["id"]]
    assert data["checks"][0]["message"]["body"] == SCAM
    assert data["checks"][0]["trace"]["steps"], "the export carries the trace"
    assert data["alerts"], "a red check made an alert"
    assert all("token" not in a for a in data["alerts"])
    assert client.get("/parents/margaret/export").json()["checks"] == data["checks"]


def test_delete_requires_confirmation(client):
    _check(client)
    assert client.delete("/api/my-data").status_code == 400
    assert client.delete("/parents/margaret/data?confirm=no").status_code == 400
    assert len(client.get("/api/my-data/export").json()["checks"]) == 1


def test_delete_all_removes_everything_but_the_spend_ledger(client):
    _check(client)
    _check(client, SAFE, sender=None)
    store = main.get_pipeline().store
    spend_before = store.spend_total()
    spend_rows = _rows(store, "spend")
    assert spend_before > 0 or spend_rows >= 0
    for table in ("analyses", "verdict_cache", "alerts", "alert_deliveries", "parents"):
        assert _rows(store, table) > 0, table

    r = client.delete("/api/my-data?confirm=yes")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert r.json()["removed"]["checks"] == 2

    for table in ("analyses", "verdict_cache", "alerts", "alert_deliveries", "parents", "telegram_links", "search_cache"):
        assert _rows(store, table) == 0, table
    assert _rows(store, "spend") == spend_rows
    assert store.spend_total() == spend_before
    data = client.get("/api/my-data/export").json()
    assert data["profile"] is None and data["checks"] == [] and data["alerts"] == []


def test_delete_all_leaves_other_parents_alone(client):
    pipeline = main.get_pipeline()
    other = pipeline.analyze(RawMessage(body=SCAM, sender="212-555-0142"), "someone-else")
    _check(client)
    client.delete("/api/my-data?confirm=yes")
    assert pipeline.store.get_analysis(other.id) is not None


def test_delete_one_check_removes_it_its_alert_and_its_cached_copy(client):
    a = _check(client)
    b = _check(client, "Your bank account is locked, verify now at http://lakeview-bank-verify.test", sender="bank@x.test")
    store = main.get_pipeline().store
    assert client.delete(f"/api/check/{a['id']}").status_code == 200
    assert store.get_analysis(a["id"]) is None
    assert store.get_analysis(b["id"]) is not None
    assert not [x for x in store.alerts_for_parent("margaret") if x["analysis_id"] == a["id"]]
    assert all(SCAM not in r["result_json"] for r in store._db.execute("SELECT result_json FROM verdict_cache"))
    assert client.delete(f"/api/check/{a['id']}").status_code == 404


def test_delete_one_check_does_not_touch_another_parents_check(client):
    pipeline = main.get_pipeline()
    other = pipeline.analyze(RawMessage(body=SCAM, sender="212-555-0142"), "someone-else")
    assert client.delete(f"/parents/margaret/checks/{other.id}").status_code == 404
    assert pipeline.store.get_analysis(other.id) is not None


def test_retention_purges_old_checks_only(client):
    old = _check(client, SCAM)
    _check(client, SAFE, sender=None)
    store = main.get_pipeline().store
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
    store._db.execute("UPDATE analyses SET created_at=? WHERE id=?", (long_ago, old["id"]))
    store._db.execute("UPDATE alerts SET created_at=? WHERE analysis_id=?", (long_ago, old["id"]))
    store._db.execute("UPDATE verdict_cache SET created_at=?", (long_ago,))
    store._db.commit()

    settings = main.get_pipeline().settings
    off = Retention(settings, store)
    assert off.sweep() == {} and _rows(store, "analyses") == 2

    on = Retention(type(settings)(retention_days=30), store)
    removed = on.sweep()
    assert removed["checks"] == 1 and removed["alerts"] == 1
    assert store.get_analysis(old["id"]) is None
    assert _rows(store, "analyses") == 1
    assert _rows(store, "parents") == 1, "retention keeps the profile"


def test_retention_env_defaults_off():
    from scamshield.config import Settings

    assert Settings.from_env({}).retention_days == 0
    assert Settings.from_env({"RETENTION_DAYS": "30"}).retention_days == 30


def test_export_parent_function_matches_route(client):
    _check(client)
    store = main.get_pipeline().store
    assert export_parent(store, "margaret")["checks"][0]["message"]["body"] == SCAM


def test_redaction_still_runs_before_the_model_through_the_api(client):
    card = "4111 1111 1111 1111"
    r = redact(f"pay with card {card} and password is hunter2")
    assert card not in r.text and "hunter2" not in r.text and r.total == 2
    seen = []
    pipeline = main.get_pipeline()
    original = pipeline.llm.complete_json
    def spy(request):
        seen.append(request.system + request.user)  # what a real client sends (context is fake-only)
        return original(request)
    pipeline.llm.complete_json = spy
    _check(client, f"Urgent: confirm your card {card} now or lose your account", sender="x@y.test")
    assert seen and not any(card in s for s in seen)


def test_family_privacy_routes_need_the_passphrase_but_the_parents_do_not(client):
    _check(client)
    signed_out = TestClient(main.app)
    assert signed_out.get("/parents/margaret/export").status_code == 401
    assert signed_out.delete("/parents/margaret/data?confirm=yes").status_code == 401
    assert signed_out.delete("/parents/margaret/checks/x").status_code == 401
    assert signed_out.get("/api/my-data/export").status_code == 200  # the parent app's own routes stay public
    assert len(client.get("/api/my-data/export").json()["checks"]) == 1
