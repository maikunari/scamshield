"""HTTP contract of the core API in offline mode, as the parent PWA uses it."""

import pytest
from fastapi.testclient import TestClient

from scamshield import main
from scamshield.models import AnalysisResult


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


def test_health_reports_offline(client):
    for path in ("/health", "/api/health"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["offline"] is True
        assert r.json()["budget_mode"] == "normal"


def test_check_grandchild_scam_is_red_with_flags_and_family_name(client):
    r = client.post(
        "/api/check",
        json={
            "sender": "212-555-0142",
            "body": "Grandma it's me, I had a car accident and need $3,000 for bail today. Please don't tell Mom. Buy gift cards and send me the codes.",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["label"] == "red"
    assert data["score"] >= 70
    assert data["red_flags"], "red screen needs red flags"
    assert all({"text", "evidence_url"} <= set(f) for f in data["red_flags"])
    assert data["safe_action"]
    assert data["family"]["name"] == "Kenji"  # from the seed profile loaded for the demo parent
    assert data["family"]["notified"] is False
    assert data["offline"] is True
    assert data["budget_mode"] == "normal"
    # Nothing meant for the family leaks to the parent screen.
    assert "family_script" not in data
    assert "trace" not in data


def test_check_uses_demo_parent_memory_for_trusted_grandson(client):
    r = client.post(
        "/api/check",
        json={"sender": "212-555-0102", "body": "Grandma it's Taro! The soccer game moved to Saturday at 2pm. Can you come?"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["label"] == "green"
    assert data["red_flags"] == []
    assert data["contact"]["status"] == "trusted"
    assert data["contact"]["name"] == "Taro Hayashi"
    assert data["contact"]["relationship"] == "grandson"


def test_check_unknown_delivery_without_fee_is_careful(client):
    r = client.post(
        "/api/check",
        json={"sender": "212-555-0175", "body": "Your order from Maple Garden Shop is out for delivery. Track: https://track-mapleparcel.example.com/8841"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "yellow"


def test_check_result_is_persisted_and_readable(client):
    r = client.post("/api/check", json={"body": "WARNING your computer is infected, call Microsoft Support at 1-800-555-0166 now"})
    analysis_id = r.json()["id"]
    full = client.get(f"/results/{analysis_id}")
    assert full.status_code == 200
    AnalysisResult.model_validate(full.json())


def test_check_is_rate_limited_per_ip(monkeypatch, tmp_path):
    monkeypatch.setenv("SCAMSHIELD_OFFLINE", "1")
    monkeypatch.setenv("SCAMSHIELD_DB", str(tmp_path / "rl.db"))
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    _reset()
    try:
        c = TestClient(main.app)
        body = {"body": "Hi, it's Yuki from the community center. Are you still joining Thursday's tea event?"}
        assert c.post("/api/check", json=body).status_code == 200
        assert c.post("/api/check", json=body).status_code == 200
        r = c.post("/api/check", json=body)
        assert r.status_code == 429
        assert "retry-after" in r.headers
    finally:
        _reset()


@pytest.mark.parametrize("payload", [{"body": ""}, {"body": "   "}, {}, {"body": "x" * (main.MAX_CHECK_CHARS + 1)}])
def test_check_rejects_empty_or_oversized(client, payload):
    assert client.post("/api/check", json=payload).status_code == 422


def test_mount_web_serves_static_export(tmp_path):
    from fastapi import FastAPI

    (tmp_path / "check").mkdir()
    (tmp_path / "index.html").write_text("<h1>home</h1>")
    (tmp_path / "check" / "index.html").write_text("<h1>check</h1>")
    application = FastAPI()

    @application.get("/api/ping")
    def ping():
        return {"pong": True}

    assert main.mount_web(application, tmp_path) is True
    assert main.mount_web(application, tmp_path / "missing") is False
    c = TestClient(application)
    assert c.get("/").text == "<h1>home</h1>"
    assert c.get("/check/").text == "<h1>check</h1>"
    assert c.get("/check", follow_redirects=True).text == "<h1>check</h1>"
    assert c.get("/api/ping").json() == {"pong": True}
