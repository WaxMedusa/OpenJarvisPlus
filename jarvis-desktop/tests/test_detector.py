from __future__ import annotations

from jarvis_desktop.config import AudioSettings
from jarvis_desktop.detector import DoubleClapDetector


def make_detector(**overrides) -> DoubleClapDetector:
    values = {
        "sample_rate": 44100,
        "block_ms": 40,
        "spike_ratio": 7.0,
        "min_rms": 0.012,
        "cooldown_s": 0.45,
        "min_gap_s": 0.05,
        "max_gap_s": 0.35,
    }
    values.update(overrides)
    return DoubleClapDetector(AudioSettings(**values))


def quiet(detector: DoubleClapDetector, start: float, seconds: float = 1.0) -> float:
    """Feed quiet blocks below the gate so the noise floor settles."""
    now = start
    for _ in range(int(seconds / 0.04)):
        now += 0.04
        detector.feed(0.0004, now)
        detector.tick(now)
    return now


def test_two_claps_become_a_double() -> None:
    detector = make_detector()
    now = quiet(detector, 100.0)

    first = detector.feed(0.6, now + 0.1)
    assert first is not None
    assert first.kind == "clap"
    assert detector.pending is True

    detector.feed(0.0004, now + 0.14)  # the room falls quiet; re-arm
    second = detector.feed(0.6, now + 0.2)
    assert second is not None
    assert second.kind == "double"
    assert second.gap is not None and 0.09 < second.gap < 0.11
    assert detector.doubles == 1
    assert detector.pending is False


def test_cooldown_suppresses_immediate_retrigger() -> None:
    detector = make_detector()
    now = quiet(detector, 200.0)
    detector.feed(0.6, now + 0.10)
    detector.feed(0.0004, now + 0.14)  # re-arm
    double = detector.feed(0.6, now + 0.20)
    assert double is not None and double.kind == "double"
    detector.feed(0.0004, now + 0.24)  # re-arm again
    suppressed = detector.feed(0.6, now + 0.25)
    assert suppressed is None
    assert detector.doubles == 1


def test_slow_second_clap_restarts_the_window() -> None:
    detector = make_detector()
    now = quiet(detector, 300.0)
    detector.feed(0.6, now + 0.1)
    detector.feed(0.0004, now + 0.15)  # re-arm
    late = detector.feed(0.6, now + 0.6)  # 0.5s apart > max_gap_s
    assert late is not None
    assert late.kind == "clap"
    assert detector.doubles == 0

    detector.feed(0.0004, now + 0.62)  # re-arm
    paired = detector.feed(0.6, now + 0.7)
    assert paired is not None
    assert paired.kind == "double"


def test_lone_clap_expires() -> None:
    detector = make_detector()
    now = quiet(detector, 400.0)
    detector.feed(0.6, now + 0.1)
    assert detector.pending is True
    detector.tick(now + 0.1 + 0.5)
    assert detector.pending is False


def test_min_rms_gate_ignores_quiet_spikes() -> None:
    detector = make_detector(min_rms=0.5)
    now = quiet(detector, 500.0)
    assert detector.feed(0.2, now + 0.1) is None
    assert detector.feed(0.2, now + 0.2) is None
    assert detector.claps == 0


def test_min_gap_ignores_double_trigger_within_one_block() -> None:
    detector = make_detector(min_gap_s=0.05)
    now = quiet(detector, 600.0)
    detector.feed(0.6, now + 0.1)
    detector.feed(0.0004, now + 0.11)  # re-arm
    too_close = detector.feed(0.6, now + 0.115)  # below min_gap_s
    assert too_close is None
    assert detector.doubles == 0
