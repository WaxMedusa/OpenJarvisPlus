"""Double-clap detection and microphone selection.

Logic is lifted from the original standalone ``jarvis.py`` and kept pure so it
can be unit-tested by feeding synthetic RMS levels. The detector is deliberately
conservative: a spike must be *spike_ratio* times the rolling noise floor, and
the two spikes must land 50-350 ms apart.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ClapConfig:
    sample_rate: int = 44100
    block_ms: int = 40
    spike_ratio: float = 7.0
    cooldown_s: float = 0.45
    min_gap_s: float = 0.05
    max_gap_s: float = 0.35
    retrigger_ratio: float = 0.55
    noise_alpha: float = 0.992
    quiet_gate_mult: float = 2.2
    min_rms: float = 0.012

    @property
    def block_samples(self) -> int:
        return max(1, int(self.sample_rate * self.block_ms / 1000))


class ClapDetector:
    """Stateful double-clap detector fed one RMS level per audio block."""

    def __init__(self, config: Optional[ClapConfig] = None) -> None:
        self.config = config or ClapConfig()
        self.noise_floor = 1e-4
        self._first_clap: Optional[float] = None
        # Negative infinity keeps the cooldown from swallowing the first clap
        # when the caller feeds relative timestamps instead of monotonic time.
        self._last_fire = float("-inf")
        self._armed = True

    def reset(self) -> None:
        self.noise_floor = 1e-4
        self._first_clap = None
        self._last_fire = float("-inf")
        self._armed = True

    def feed(self, rms: float, now: Optional[float] = None) -> bool:
        """Return True exactly when a double clap is recognised."""
        cfg = self.config
        level = max(0.0, float(rms))
        moment = time.monotonic() if now is None else now

        if level < self.noise_floor * cfg.quiet_gate_mult:
            self.noise_floor = (
                cfg.noise_alpha * self.noise_floor + (1.0 - cfg.noise_alpha) * level
            )
            self.noise_floor = max(self.noise_floor, 1e-7)

        threshold = max(self.noise_floor * cfg.spike_ratio, cfg.min_rms)
        if level < threshold * cfg.retrigger_ratio:
            self._armed = True

        if (
            self._armed
            and level >= threshold
            and (moment - self._last_fire) >= cfg.cooldown_s
        ):
            self._armed = False
            if self._first_clap is None:
                self._first_clap = moment
                return False
            gap = moment - self._first_clap
            if gap < cfg.min_gap_s:
                return False
            if gap <= cfg.max_gap_s:
                self._first_clap = None
                self._last_fire = moment
                return True
            # Too slow to be a double clap: this spike becomes the new first clap.
            self._first_clap = moment
        return False


def pcm_rms_int16(data: Any) -> float:
    """RMS of a block of 16-bit PCM, normalised to 0..1."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy is part of the speech extra
        return 0.0
    block = np.asarray(data)
    if block.size == 0:
        return 0.0
    if block.dtype != np.int16:
        # RawInputStream hands back a buffer object sometimes.
        try:
            block = np.frombuffer(block.tobytes(), dtype=np.int16)
        except (AttributeError, ValueError):
            return 0.0
    samples = block.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples**2)))


def _input_devices(sd: Any) -> list[tuple[int, dict]]:
    return [
        (index, device)
        for index, device in enumerate(sd.query_devices())
        if device.get("max_input_channels", 0) >= 1
    ]


def _resolve_device(sd: Any, spec: str) -> int:
    spec = spec.strip()
    if spec.isdigit():
        index = int(spec)
        sd.query_devices(index)
        return index
    needle = spec.lower()
    for index, device in _input_devices(sd):
        if needle in device.get("name", "").lower():
            return index
    raise ValueError(f"No input device matches {spec!r}")


def _probe(
    sd: Any, device: int, config: ClapConfig, seconds: float = 0.5
) -> Optional[float]:
    try:
        with sd.InputStream(
            device=device,
            samplerate=config.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=config.block_samples,
        ) as stream:
            peak = 0.0
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                data, _ = stream.read(config.block_samples)
                peak = max(peak, pcm_rms_int16(data))
            return peak
    except Exception:
        return None


def choose_input_device(sd: Any, config: Optional[ClapConfig] = None) -> Optional[int]:
    """Pick a working microphone, honouring an explicit override.

    ``JARVIS_INPUT_DEVICE`` (the name used by the original script) and
    ``OPENJARVIS_INPUT_DEVICE`` both accept an index or a name substring.
    """
    cfg = config or ClapConfig()
    override = (
        os.environ.get("OPENJARVIS_INPUT_DEVICE")
        or os.environ.get("JARVIS_INPUT_DEVICE")
        or ""
    ).strip()
    if override:
        try:
            return _resolve_device(sd, override)
        except Exception:
            print(
                f"[wake] Ignoring mic override {override!r}: not found.",
                file=sys.stderr,
            )
    try:
        default = sd.default.device[0]
    except Exception:
        default = None
    if default is not None and default >= 0:
        peak = _probe(sd, default, cfg)
        if peak is not None and peak >= 0.001:
            return int(default)
    best_index: Optional[int] = None
    best_peak = -1.0
    for index, _device in _input_devices(sd):
        if default is not None and index == default:
            continue
        peak = _probe(sd, index, cfg)
        if peak is not None and peak > best_peak:
            best_peak = peak
            best_index = index
    if best_index is not None and best_peak >= 0.001:
        return best_index
    if default is not None and default >= 0:
        return int(default)
    return None


__all__ = ["ClapConfig", "ClapDetector", "choose_input_device", "pcm_rms_int16"]
