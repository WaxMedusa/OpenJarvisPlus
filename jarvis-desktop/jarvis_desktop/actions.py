"""The double-clap action pipeline: song, Chrome tabs, voice, Cursor."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import wave
from pathlib import Path

import numpy as np

from . import config, win32
from .paths import data_dir
from .state import Hub

log = logging.getLogger("jarvis.actions")

_sequence_lock = threading.Lock()


def _emit(hub: Hub, step: str, status: str, detail: str = "") -> None:
    hub.publish({"type": "action", "step": step, "status": status, "detail": detail})


def run_sequence(
    settings: config.Settings, hub: Hub, *, source: str = "double clap"
) -> None:
    """Run the launch sequence once; overlapping triggers are skipped."""
    if not _sequence_lock.acquire(blocking=False):
        _emit(hub, "sequence", "skip", "A sequence is already running")
        return
    started = time.monotonic()
    hub.publish({"type": "sequence", "state": "started", "source": source})
    try:
        seq = settings.sequence

        if seq.open_song and seq.song_uri.strip():
            _emit(hub, "song", "ok", win32.open_uri(seq.song_uri))
        else:
            _emit(hub, "song", "skip", "Disabled")

        for target in seq.chrome_targets:
            if not target.enabled:
                continue
            detail = win32.open_url_in_chrome(
                target.url, monitor=target.monitor, fullscreen=target.fullscreen
            )
            _emit(hub, target.label or target.id, "ok", detail)

        voice = settings.voice
        if voice.enabled and voice.phrase.strip():
            if voice.after_song_delay_s:
                time.sleep(voice.after_song_delay_s)
            threading.Thread(
                target=speak,
                args=(settings, hub),
                name="jarvis-voice",
                daemon=True,
            ).start()
        else:
            _emit(hub, "voice", "skip", "Disabled")

        if seq.focus_cursor or seq.new_cursor_window:
            _emit(
                hub,
                "cursor",
                "ok",
                win32.focus_cursor(
                    focus=seq.focus_cursor,
                    new_window=seq.new_cursor_window,
                    fullscreen=seq.cursor_fullscreen,
                ),
            )
        else:
            _emit(hub, "cursor", "skip", "Disabled")
    finally:
        hub.publish(
            {
                "type": "sequence",
                "state": "finished",
                "seconds": round(time.monotonic() - started, 2),
            }
        )
        _sequence_lock.release()


# --- voice -----------------------------------------------------------------


def _cache_dir() -> Path:
    path = data_dir() / "voice-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(text: str, voice_id: str, model_id: str, output_format: str) -> Path:
    key = f"{text}|{voice_id}|{model_id}|{output_format}".encode()
    return _cache_dir() / f"{hashlib.sha256(key).hexdigest()[:24]}.wav"


def _play_pcm(raw: bytes, sample_rate: int) -> None:
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    import sounddevice as sd

    sd.play(samples, sample_rate)
    sd.wait()


def _play_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                return False
            frames = handle.readframes(handle.getnframes())
            rate = handle.getframerate()
    except (OSError, wave.Error):
        return False
    if not frames:
        return False
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    import sounddevice as sd

    sd.play(samples, rate)
    sd.wait()
    return True


def _save_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with wave.open(str(tmp), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _pcm_rate(output_format: str) -> int:
    if output_format.startswith("pcm_"):
        try:
            return int(output_format.split("_", maxsplit=1)[1])
        except (ValueError, IndexError):
            pass
    return 24000


def speak(settings: config.Settings, hub: Hub) -> None:
    """Synthesize (or replay cached) ElevenLabs audio for the configured phrase."""
    voice = settings.voice
    text = voice.phrase.strip()
    if not text:
        _emit(hub, "voice", "skip", "Phrase is empty")
        return
    if not voice.voice_id.strip():
        _emit(hub, "voice", "error", "Set the ElevenLabs voice ID first")
        return

    api_key = config.get_api_key()
    if not api_key:
        _emit(hub, "voice", "error", "Set the ElevenLabs API key first")
        return

    sample_rate = _pcm_rate(voice.output_format)
    cache = _cache_path(text, voice.voice_id, voice.model_id, voice.output_format)
    if not voice.output_format.strip().startswith("pcm_"):
        _emit(hub, "voice", "error", "output_format must be a pcm_* format")
        return

    if voice.cache and cache.is_file():
        _emit(hub, "voice", "ok", "Playing cached audio")
        try:
            if _play_wav(cache):
                return
        except Exception as exc:
            log.warning("Cached playback failed: %s", exc)
            _emit(hub, "voice", "error", f"Cached audio could not play: {exc}")

    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        _emit(hub, "voice", "error", "ElevenLabs client is not installed")
        return

    _emit(hub, "voice", "ok", "Synthesizing with ElevenLabs…")
    try:
        client = ElevenLabs(api_key=api_key)
        chunks = client.text_to_speech.convert(
            voice_id=voice.voice_id.strip(),
            text=text,
            model_id=voice.model_id.strip(),
            output_format=voice.output_format.strip(),
        )
        raw = b"".join(chunks)
    except Exception as exc:
        _emit(hub, "voice", "error", f"ElevenLabs failed: {exc}")
        return
    if not raw:
        _emit(hub, "voice", "error", "ElevenLabs returned empty audio")
        return

    if voice.cache:
        try:
            _save_wav(cache, raw, sample_rate)
        except OSError as exc:
            log.warning("Could not cache voice audio: %s", exc)

    try:
        _play_pcm(raw, sample_rate)
    except Exception as exc:
        _emit(hub, "voice", "error", f"Playback failed: {exc}")
        return
    _emit(hub, "voice", "ok", "Spoken")
