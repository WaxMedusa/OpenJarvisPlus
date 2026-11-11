"""Persisted settings and the ElevenLabs credential.

Settings live in ``<data_dir>/settings.json``; the API key is kept out of that
file in ``<data_dir>/secrets.json`` and is never returned to the browser.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from .paths import data_dir

log = logging.getLogger("jarvis.config")

SETTINGS_FILE = "settings.json"
SECRETS_FILE = "secrets.json"
DEFAULT_SONG_URI = (
    "https://open.spotify.com/track/6sstcO05hV8WqTVz0Xm5er?si=14f82650f0064b2f"
)
DEFAULT_PHRASE = (
    "Welcome home, sir. All systems are online. What are we building today?"
)

_LOCK = threading.Lock()


class AudioSettings(BaseModel):
    input_device: str = ""
    sample_rate: int = Field(default=44100, ge=8000, le=192000)
    block_ms: int = Field(default=40, ge=10, le=200)
    spike_ratio: float = Field(default=7.0, ge=1.5, le=50.0)
    min_rms: float = Field(default=0.012, ge=0.0, le=1.0)
    cooldown_s: float = Field(default=0.45, ge=0.05, le=10.0)
    min_gap_s: float = Field(default=0.05, ge=0.01, le=2.0)
    max_gap_s: float = Field(default=0.35, ge=0.05, le=3.0)

    @model_validator(mode="after")
    def _gap_window(self) -> "AudioSettings":
        if self.min_gap_s >= self.max_gap_s:
            raise ValueError("min_gap_s must be smaller than max_gap_s")
        return self


class ChromeTarget(BaseModel):
    id: str
    label: str
    url: str
    enabled: bool = False
    monitor: int = Field(default=1, ge=1, le=16)
    fullscreen: bool = False

    @field_validator("id", "label", "url")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("url")
    @classmethod
    def _url_scheme(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class SequenceSettings(BaseModel):
    open_song: bool = True
    song_uri: str = DEFAULT_SONG_URI
    chrome_targets: list[ChromeTarget] = Field(
        default_factory=lambda: [
            ChromeTarget(
                id="claude",
                label="Claude",
                url="https://claude.ai/new",
                enabled=False,
                monitor=1,
            ),
            ChromeTarget(
                id="binance",
                label="Binance BTC",
                url="https://www.binance.com/en/trade/BTC_USDT",
                enabled=False,
                monitor=3,
            ),
        ]
    )
    focus_cursor: bool = True
    new_cursor_window: bool = False
    cursor_fullscreen: bool = True

    @field_validator("song_uri")
    @classmethod
    def _song_uri(cls, value: str) -> str:
        value = value.strip()
        if value and not value.lower().startswith(
            ("http://", "https://", "spotify:", "file://")
        ):
            raise ValueError("song_uri must be http(s), spotify: or file://")
        return value


class VoiceSettings(BaseModel):
    enabled: bool = True
    phrase: str = DEFAULT_PHRASE
    after_song_delay_s: float = Field(default=1.0, ge=0.0, le=30.0)
    cache: bool = True
    voice_id: str = ""
    model_id: str = "eleven_multilingual_v2"
    output_format: str = "pcm_24000"


class Settings(BaseModel):
    auto_start_listener: bool = True
    audio: AudioSettings = Field(default_factory=AudioSettings)
    sequence: SequenceSettings = Field(default_factory=SequenceSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def settings_path() -> Path:
    return data_dir() / SETTINGS_FILE


def secrets_path() -> Path:
    return data_dir() / SECRETS_FILE


def load_settings() -> Settings:
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s (%s); using defaults", path, exc)
        return Settings()
    try:
        return Settings.model_validate(raw)
    except ValueError as exc:
        log.warning("Invalid settings in %s (%s); using defaults", path, exc)
        return Settings()


def save_settings(settings: Settings) -> None:
    text = json.dumps(settings.model_dump(), indent=2, ensure_ascii=False)
    with _LOCK:
        _atomic_write(settings_path(), text)


def get_api_key() -> str:
    """Stored key first, then the process environment."""
    path = secrets_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    key = str(data.get("elevenlabs_api_key") or "").strip()
    return key or (os.environ.get("ELEVENLABS_API_KEY") or "").strip()


def set_api_key(key: str | None) -> None:
    key = (key or "").strip()
    payload = json.dumps({"elevenlabs_api_key": key}, indent=2)
    with _LOCK:
        _atomic_write(secrets_path(), payload)
        try:
            os.chmod(secrets_path(), 0o600)
        except OSError:
            pass


def public_settings(settings: Settings) -> dict:
    """Settings safe to send to the browser (never includes the API key)."""
    data = settings.model_dump()
    data["voice"]["api_key_set"] = bool(get_api_key())
    data["voice"]["api_key_hint"] = _masked_key_hint()
    return data


def _masked_key_hint() -> str:
    key = get_api_key()
    if not key:
        return ""
    return f"…{key[-4:]}" if len(key) > 4 else "set"
