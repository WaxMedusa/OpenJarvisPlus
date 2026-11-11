"""Pure double-clap detection logic, independent of any audio device."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import AudioSettings

QUIET_GATE_MULT = 2.2
RETRIGGER_RATIO = 0.55
NOISE_FLOOR_ALPHA = 0.992
INITIAL_NOISE_FLOOR = 1e-4
MIN_NOISE_FLOOR = 1e-7


@dataclass(frozen=True)
class Hit:
    kind: str  # "clap" | "double"
    level: float
    threshold: float
    noise_floor: float
    gap: float | None = None


class DoubleClapDetector:
    """Turns a stream of RMS levels into clap / double-clap hits.

    Kept pure so it can be tested without PortAudio: call :meth:`feed` with an
    RMS level and a monotonic timestamp.
    """

    def __init__(self, audio: AudioSettings) -> None:
        self.cfg = audio
        self.noise_floor = INITIAL_NOISE_FLOOR
        self.threshold = max(self.noise_floor * audio.spike_ratio, audio.min_rms)
        self.claps = 0
        self.doubles = 0
        self.last_double_at: float | None = None
        self._armed = True
        self._first: float | None = None
        self._last_double = 0.0

    @property
    def pending(self) -> bool:
        return self._first is not None

    def feed(self, level: float, now: float | None = None) -> Hit | None:
        now = time.monotonic() if now is None else now
        cfg = self.cfg

        quiet_gate = self.noise_floor * QUIET_GATE_MULT
        if level < quiet_gate:
            self.noise_floor = (
                NOISE_FLOOR_ALPHA * self.noise_floor + (1.0 - NOISE_FLOOR_ALPHA) * level
            )
            self.noise_floor = max(self.noise_floor, MIN_NOISE_FLOOR)

        self.threshold = max(self.noise_floor * cfg.spike_ratio, cfg.min_rms)

        if level < self.threshold * RETRIGGER_RATIO:
            self._armed = True

        if not self._armed:
            return None
        if level < self.threshold:
            return None
        if now - self._last_double < cfg.cooldown_s:
            return None

        self._armed = False
        self.claps += 1

        if self._first is None:
            self._first = now
            return Hit("clap", level, self.threshold, self.noise_floor)

        gap = now - self._first
        if gap < cfg.min_gap_s:
            return None
        if gap <= cfg.max_gap_s:
            self._first = None
            self._last_double = now
            self.last_double_at = now
            self.doubles += 1
            return Hit("double", level, self.threshold, self.noise_floor, gap)

        self._first = now
        return Hit("clap", level, self.threshold, self.noise_floor, gap)

    def tick(self, now: float | None = None) -> None:
        """Expire a lone first clap so a later one cannot pair with it."""
        now = time.monotonic() if now is None else now
        if self._first is not None and now - self._first > self.cfg.max_gap_s:
            self._first = None

    def reset(self) -> None:
        self._first = None
        self._armed = True
        self._last_double = 0.0

    def snapshot(self) -> dict:
        return {
            "claps": self.claps,
            "doubles": self.doubles,
            "pending": self.pending,
            "noise_floor": self.noise_floor,
            "threshold": self.threshold,
        }
