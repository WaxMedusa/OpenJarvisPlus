from __future__ import annotations

import numpy as np

from openjarvis.speech.clap import ClapConfig, ClapDetector, pcm_rms_int16


def test_double_clap_within_window_fires() -> None:
    detector = ClapDetector(ClapConfig())
    assert detector.feed(0.001, now=0.0) is False
    assert detector.feed(0.4, now=1.0) is False  # first clap
    assert detector.feed(0.001, now=1.02) is False  # retrigger
    assert detector.feed(0.4, now=1.12) is True  # second clap, 120 ms later


def test_single_clap_does_not_fire() -> None:
    detector = ClapDetector(ClapConfig())
    assert detector.feed(0.001, now=0.0) is False
    assert detector.feed(0.4, now=1.0) is False
    assert detector.feed(0.001, now=1.05) is False


def test_slow_claps_do_not_fire() -> None:
    detector = ClapDetector(ClapConfig())
    assert detector.feed(0.4, now=1.0) is False
    assert detector.feed(0.001, now=1.4) is False
    # 400 ms apart is outside the double-clap window, so this re-anchors.
    assert detector.feed(0.4, now=1.8) is False
    assert detector.feed(0.001, now=1.85) is False
    # The 1.8/1.9 pair *is* 100 ms apart, proving the re-anchor works.
    assert detector.feed(0.4, now=1.9) is True


def test_cooldown_prevents_immediate_refire() -> None:
    detector = ClapDetector(ClapConfig(cooldown_s=2.0))
    assert detector.feed(0.4, now=1.0) is False
    assert detector.feed(0.001, now=1.05) is False
    assert detector.feed(0.4, now=1.1) is True
    assert detector.feed(0.001, now=1.2) is False
    assert detector.feed(0.4, now=1.3) is False  # within cooldown of the fire at 1.1
    assert detector.feed(0.4, now=1.4) is False


def test_threshold_follows_the_noise_floor() -> None:
    detector = ClapDetector(ClapConfig(min_rms=0.0))
    detector.noise_floor = 0.01  # a noisy room
    # 0.05 is below 7x the floor, so it is not a clap.
    assert detector.feed(0.05, now=0.9) is False
    assert detector.feed(0.001, now=0.95) is False
    # 0.1 clears the elevated threshold.
    assert detector.feed(0.1, now=1.0) is False  # first clap
    assert detector.feed(0.001, now=1.05) is False
    assert detector.feed(0.1, now=1.15) is True  # second clap


def test_pcm_rms_int16() -> None:
    silence = np.zeros(441, dtype=np.int16)
    assert pcm_rms_int16(silence) == 0.0
    loud = np.full(441, 32767, dtype=np.int16)
    assert 0.99 < pcm_rms_int16(loud) <= 1.0
