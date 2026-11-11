from __future__ import annotations

import json

import pytest
from jarvis_desktop import config
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    return tmp_path


def test_settings_round_trip(data_dir) -> None:
    settings = config.load_settings()
    settings.voice.voice_id = "voice-123"
    settings.sequence.open_song = False
    config.save_settings(settings)

    loaded = config.load_settings()
    assert loaded.voice.voice_id == "voice-123"
    assert loaded.sequence.open_song is False
    assert (data_dir / config.SETTINGS_FILE).is_file()


def test_invalid_gap_window_rejected() -> None:
    with pytest.raises(ValidationError):
        config.AudioSettings(min_gap_s=0.5, max_gap_s=0.2)


def test_api_key_is_secret_and_masked(data_dir, monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert config.get_api_key() == ""

    config.set_api_key("sk-super-secret-9876")
    assert config.get_api_key() == "sk-super-secret-9876"

    public = config.public_settings(config.load_settings())
    assert public["voice"]["api_key_set"] is True
    assert public["voice"]["api_key_hint"].endswith("9876")
    assert "sk-super-secret" not in json.dumps(public)

    config.set_api_key(None)
    assert config.get_api_key() == ""


def test_corrupt_settings_fall_back_to_defaults(data_dir) -> None:
    (data_dir / config.SETTINGS_FILE).write_text("{not json", encoding="utf-8")
    assert config.load_settings().audio.sample_rate == 44100


def test_chrome_target_requires_http_url() -> None:
    with pytest.raises(ValidationError):
        config.ChromeTarget(id="x", label="X", url="file:///etc/passwd")
