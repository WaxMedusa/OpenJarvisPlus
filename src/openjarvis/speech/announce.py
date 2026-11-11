"""Speech-friendly text and the ElevenLabs speaker used by ``jarvis wake``.

Two jobs:

``speakable`` turns a terminal answer (markdown, code, tables, URLs) into one
to three sentences worth listening to. The full answer still goes to the
terminal — the voice only gets the summary, which keeps latency and ElevenLabs
credits down.

``Speaker`` resolves a TTS backend once, synthesises WAV, plays it through
``voice_io.play_wav`` and exposes a ``speaking`` event so the microphone can
mute itself while Jarvis talks (otherwise the speakers trigger clap detection).
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from openjarvis.core.paths import get_config_dir

_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*_#>]+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_MULTISPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def speakable(text: str, max_chars: int = 320) -> str:
    """Return a short, plain-text version of *text* suitable for TTS."""
    if not text:
        return ""
    cleaned = _FENCED_CODE.sub(" ", text)
    cleaned = _TABLE_ROW.sub(" ", cleaned)
    cleaned = _LINK.sub(r"\1", cleaned)
    cleaned = _URL.sub(" ", cleaned)
    cleaned = _INLINE_CODE.sub(r"\1", cleaned)
    cleaned = _MARKUP.sub(" ", cleaned)
    cleaned = cleaned.replace("—", ", ").replace("–", ", ")
    cleaned = _MULTISPACE.sub(" ", cleaned).strip()
    if not cleaned:
        return ""
    sentences = [part.strip() for part in _SENTENCE.split(cleaned) if part.strip()]
    spoken = ""
    for sentence in sentences[:3]:
        candidate = f"{spoken} {sentence}".strip()
        if len(candidate) > max_chars and spoken:
            break
        spoken = candidate
        if len(spoken) >= max_chars * 0.6:
            break
    if len(spoken) > max_chars:
        spoken = spoken[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return spoken


class Speaker:
    """ElevenLabs (or any configured TTS backend) with a mute-friendly state."""

    def __init__(
        self,
        config: Any = None,
        *,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._on_status = on_status
        self._resolved = False
        self._backend: Any = None
        self._voice_id = ""
        self._speed = 1.0
        self._error = ""
        self._lock = threading.Lock()
        self._speaking = threading.Event()
        self._stop = threading.Event()
        self._cache_dir = get_config_dir() / "wake" / "tts_cache"

    # -- state -------------------------------------------------------------
    @property
    def speaking(self) -> threading.Event:
        return self._speaking

    @property
    def error(self) -> str:
        return self._error

    @property
    def available(self) -> bool:
        return self._resolve()

    def _status(self, message: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(message)
            except Exception:
                pass

    def _resolve(self) -> bool:
        if self._resolved:
            return self._backend is not None
        self._resolved = True
        try:
            from openjarvis.speech._tts_discovery import (
                default_voice_for,
                get_tts_backend,
                voice_preferences,
            )

            config = self._config
            if config is None:
                from openjarvis.core.config import load_config

                config = load_config()
            preferred, voice_id, speed = voice_preferences(config)
            backend = get_tts_backend(preferred)
            if backend is None:
                self._error = "No healthy TTS backend is configured."
                return False
            active = getattr(backend, "backend_id", "") or preferred
            self._backend = backend
            self._voice_id = (
                voice_id if active == preferred else default_voice_for(active)
            )
            self._speed = speed
            return True
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            return False

    # -- synthesis ---------------------------------------------------------
    def _cache_file(self, text: str, cache_key: str) -> Path:
        key = f"{cache_key}|{text}|{self._voice_id}|{self._speed}".encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()[:24]
        return self._cache_dir / f"{digest}.wav"

    def say(self, text: str, *, cache_key: str = "") -> bool:
        """Synthesise and play *text* synchronously. Returns success."""
        text = (text or "").strip()
        if not text:
            return False
        if not self._resolve() or self._backend is None:
            self._status(f"Voice unavailable: {self._error}")
            return False
        with self._lock:
            self._stop.clear()
            audio: Optional[bytes] = None
            cache_file = self._cache_file(text, cache_key) if cache_key else None
            if cache_file is not None and cache_file.is_file():
                try:
                    audio = cache_file.read_bytes()
                except OSError:
                    audio = None
            if audio is None:
                try:
                    result = self._backend.synthesize(
                        text,
                        voice_id=self._voice_id,
                        speed=self._speed,
                        output_format="wav",
                    )
                    audio = result.audio
                    sample_rate = int(getattr(result, "sample_rate", 24000) or 24000)
                except Exception as exc:
                    self._error = f"{type(exc).__name__}: {exc}"
                    self._status(f"Speech failed: {self._error}")
                    return False
                if cache_file is not None:
                    try:
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        cache_file.write_bytes(audio)
                    except OSError:
                        pass
            else:
                sample_rate = 24000
            try:
                from openjarvis.speech.voice_io import play_wav

                self._speaking.set()
                self._status("speaking")
                play_wav(audio, sample_rate=sample_rate)
                return True
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                self._status(f"Playback failed: {self._error}")
                return False
            finally:
                self._speaking.clear()
                self._status("idle")

    def say_async(self, text: str, *, cache_key: str = "") -> None:
        thread = threading.Thread(
            target=self.say,
            args=(text,),
            kwargs={"cache_key": cache_key},
            name="jarvis-speak",
            daemon=True,
        )
        thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass
        self._speaking.clear()


__all__ = ["Speaker", "speakable"]
