from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from jarvis_desktop import config
from jarvis_desktop.app import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    settings = config.Settings()
    settings.auto_start_listener = False
    settings.sequence.open_song = False
    settings.sequence.focus_cursor = False
    settings.sequence.chrome_targets = []
    settings.voice.enabled = False
    config.set_api_key(None)
    config.save_settings(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_status_never_leaks_key(client) -> None:
    config.set_api_key("sk-do-not-leak-1234")
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert body["settings"]["voice"]["api_key_set"] is True
    assert "sk-do-not-leak" not in response.text


def test_put_settings_persists(client, tmp_path) -> None:
    payload = client.get("/api/status").json()["settings"]
    payload["audio"]["spike_ratio"] = 9.5
    payload["voice"]["phrase"] = "Testing, one two."

    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True

    stored = config.load_settings()
    assert stored.audio.spike_ratio == 9.5
    assert stored.voice.phrase == "Testing, one two."


def test_put_settings_rejects_invalid_window(client) -> None:
    payload = client.get("/api/status").json()["settings"]
    payload["audio"]["min_gap_s"] = 0.9
    payload["audio"]["max_gap_s"] = 0.2
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 422


def test_listener_start_reports_missing_audio(client) -> None:
    response = client.post("/api/listener/start")
    assert response.status_code == 200
    body = response.json()
    assert "ok" in body
    assert "listener" in body


def test_manual_trigger_runs_disabled_sequence(client) -> None:
    response = client.post("/api/trigger")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_cross_origin_mutation_rejected(client) -> None:
    response = client.post("/api/trigger", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_websocket_sends_snapshot(client) -> None:
    with client.websocket_connect("/ws") as socket:
        message = socket.receive_json()
        assert message["type"] == "snapshot"
        assert "listener" in message["status"]
