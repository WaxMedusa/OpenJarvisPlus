"""Microphone capture thread and device discovery."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from . import config
from .detector import DoubleClapDetector
from .state import Hub

log = logging.getLogger("jarvis.listener")

INPUT_PROBE_S = 0.5
INPUT_SILENT_RMS = 0.001
LEVEL_PUBLISH_S = 0.06


def _load_sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # ImportError or missing PortAudio library
        raise RuntimeError(
            "Microphone support is unavailable (sounddevice/PortAudio: "
            f"{exc}). Install the audio dependencies and try again."
        ) from exc
    return sd


def rms_mono(block: np.ndarray) -> float:
    data = np.asarray(block, dtype=np.float64)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(data**2)))


def list_devices() -> tuple[list[dict], str | None]:
    """Input devices as JSON-safe dicts, plus an error string if unavailable."""
    try:
        sd = _load_sounddevice()
    except RuntimeError as exc:
        return [], str(exc)
    try:
        devices = sd.query_devices()
        default = sd.default.device
    except Exception as exc:
        return [], f"Could not query audio devices: {exc}"

    default_index = None
    if isinstance(default, (list, tuple)) and default:
        default_index = default[0]
    elif isinstance(default, int):
        default_index = default

    out = []
    for index, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) < 1:
            continue
        out.append(
            {
                "index": index,
                "name": str(dev.get("name", f"Input {index}")),
                "channels": int(dev.get("max_input_channels", 0)),
                "default": index == default_index,
            }
        )
    return out, None


def _resolve_index(sd, spec: str) -> int:
    spec = spec.strip()
    if spec.isdigit():
        index = int(spec)
        sd.query_devices(index)
        return index
    needle = spec.lower()
    for index, dev in enumerate(sd.query_devices()):
        if (
            int(dev.get("max_input_channels", 0)) >= 1
            and needle in str(dev.get("name", "")).lower()
        ):
            return index
    raise ValueError(f"No input device matches {spec!r}")


def _probe(sd, device: int, samplerate: int, blocksize: int) -> float | None:
    try:
        with sd.InputStream(
            device=device,
            samplerate=samplerate,
            channels=1,
            dtype="float32",
            blocksize=blocksize,
        ) as stream:
            peak = 0.0
            deadline = time.monotonic() + INPUT_PROBE_S
            while time.monotonic() < deadline:
                data, _ = stream.read(blocksize)
                peak = max(peak, rms_mono(data))
            return peak
    except Exception:
        return None


def choose_device(sd, audio: config.AudioSettings, blocksize: int) -> int:
    """Prefer the configured device, then the default, then the loudest input."""
    override = audio.input_device.strip()
    if override:
        return _resolve_index(sd, override)

    default = (
        sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else None
    )
    if isinstance(default, int) and default >= 0:
        peak = _probe(sd, default, audio.sample_rate, blocksize)
        if peak is not None and peak >= INPUT_SILENT_RMS:
            return default
        log.warning("Default microphone is silent or unavailable; scanning inputs")

    best_index = None
    best_peak = -1.0
    for index, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) < 1 or index == default:
            continue
        peak = _probe(sd, index, audio.sample_rate, blocksize)
        if peak is not None and peak > best_peak:
            best_peak, best_index = peak, index
    if best_index is not None and best_peak >= INPUT_SILENT_RMS:
        return best_index
    if isinstance(default, int) and default >= 0:
        return default
    for index, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) >= 1:
            return index
    raise RuntimeError("No microphone input devices were found.")


class Listener:
    """Owns the capture thread; publishes levels, claps and double claps."""

    def __init__(
        self,
        hub: Hub,
        on_double: Callable[[config.Settings], None],
    ) -> None:
        self._hub = hub
        self._on_double = on_double
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._stream = None
        self._detector: DoubleClapDetector | None = None
        self._device_name = ""
        self._error: str | None = None
        self._level = 0.0
        self._started_at: float | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._stop.is_set())

    def status(self) -> dict:
        detector = self._detector
        return {
            "listening": self.running,
            "error": self._error,
            "device": self._device_name,
            "level": round(self._level, 5),
            "started_at": self._started_at,
            **(
                detector.snapshot()
                if detector
                else {
                    "claps": 0,
                    "doubles": 0,
                    "pending": False,
                    "noise_floor": None,
                    "threshold": None,
                }
            ),
        }

    def start(self) -> tuple[bool, str | None]:
        with self._lock:
            if self.running:
                return True, None
            try:
                _load_sounddevice()
            except RuntimeError as exc:
                self._error = str(exc)
                self._hub.publish(
                    {"type": "listener", "state": "error", "error": str(exc)}
                )
                return False, str(exc)

            self._stop.clear()
            self._ready.clear()
            self._error = None
            thread = threading.Thread(
                target=self._run, name="jarvis-listener", daemon=True
            )
            self._thread = thread
            thread.start()

        if not self._ready.wait(8.0):
            self.stop()
            message = self._error or "Timed out while opening the microphone."
            return False, message
        return (self.running, self._error)

    def stop(self) -> None:
        self._stop.set()
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None
        self._stream = None

    def _fail(self, message: str) -> None:
        self._error = message
        log.error("%s", message)
        self._hub.publish({"type": "listener", "state": "error", "error": message})
        self._ready.set()

    def _run(self) -> None:
        settings = config.load_settings()
        audio = settings.audio
        detector = DoubleClapDetector(audio)
        self._detector = detector
        blocksize = max(int(audio.sample_rate * audio.block_ms / 1000), 1)

        try:
            sd = _load_sounddevice()
            device = choose_device(sd, audio, blocksize)
            device_name = str(sd.query_devices(device)["name"])
            with sd.InputStream(
                device=device,
                samplerate=audio.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
            ) as stream:
                self._stream = stream
                self._device_name = device_name
                self._started_at = time.time()
                self._error = None
                self._hub.publish(
                    {
                        "type": "listener",
                        "state": "listening",
                        "device": device_name,
                        "sample_rate": audio.sample_rate,
                    }
                )
                self._ready.set()

                last_level = 0.0
                while not self._stop.is_set():
                    data, overflowed = stream.read(blocksize)
                    now = time.monotonic()
                    level = rms_mono(data)
                    self._level = level
                    hit = detector.feed(level, now)
                    detector.tick(now)

                    if hit is not None:
                        self._hub.publish(
                            {
                                "type": "clap" if hit.kind == "clap" else "double-clap",
                                "level": round(hit.level, 5),
                                "threshold": round(hit.threshold, 5),
                                "gap": round(hit.gap, 3)
                                if hit.gap is not None
                                else None,
                            }
                        )
                        if hit.kind == "double":
                            threading.Thread(
                                target=self._fire,
                                args=(config.load_settings(),),
                                name="jarvis-sequence",
                                daemon=True,
                            ).start()

                    if now - last_level >= LEVEL_PUBLISH_S:
                        last_level = now
                        self._hub.publish(
                            {
                                "type": "level",
                                "level": round(level, 5),
                                "threshold": round(detector.threshold, 5),
                                **detector.snapshot(),
                            }
                        )
                    if overflowed:
                        log.debug("Input overflow; consider a larger block_ms")
        except Exception as exc:
            if self._stop.is_set():
                log.info("Listener stopped")
            else:
                self._fail(f"Microphone error: {exc}")
        finally:
            self._stream = None
            self._hub.publish({"type": "listener", "state": "stopped"})
            self._ready.set()

    def _fire(self, settings: config.Settings) -> None:
        try:
            self._on_double(settings)
        except Exception:
            log.exception("Double-clap sequence failed")
            self._hub.publish(
                {
                    "type": "action",
                    "step": "sequence",
                    "status": "error",
                    "detail": "Sequence crashed; see the log file.",
                }
            )
