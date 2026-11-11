"""The "coming home" ritual: song, apps, and the ElevenLabs welcome line.

This preserves the behaviour of the original ``jarvis.py`` double-clap script:
open the track, optionally open Chrome pages on chosen monitors, focus or
launch Cursor and send F11, then speak the cached welcome phrase. Everything is
best-effort and reported, never fatal.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from openjarvis.pc import windows


def _wake(config: Any) -> Any:
    return getattr(config, "wake", None)


def run_welcome_ritual(
    config: Any,
    speaker: Any,
    emit: Callable[[str], None],
    *,
    dry_run: bool = False,
) -> None:
    """Run the arrival sequence. *emit* receives human-readable progress."""
    wake = _wake(config)
    song_uri = str(getattr(wake, "song_uri", "") or "").strip()
    open_urls = list(getattr(wake, "open_urls", []) or [])
    focus_cursor = bool(getattr(wake, "focus_cursor", True))
    cursor_fullscreen = bool(getattr(wake, "cursor_fullscreen", True))
    phrase = str(getattr(wake, "welcome_phrase", "") or "").strip()
    delay = float(getattr(wake, "after_song_delay_s", 1.0) or 0)
    chrome_monitor = int(getattr(wake, "chrome_monitor", 1) or 1)

    if dry_run:
        emit(
            "[dry run] Would play "
            f"{song_uri or '(no track configured)'}, open {len(open_urls)} page(s), "
            f"focus Cursor={focus_cursor}, and say {phrase[:60]!r}."
        )
        return

    if song_uri:
        ok, message = windows.open_target(song_uri)
        emit(message if ok else f"Could not start the track: {message}")
    else:
        emit("No track configured; skipping the music.")

    for url in open_urls:
        if not url:
            continue
        ok, message = windows.open_url_in_chrome(url, monitor=chrome_monitor)
        emit(message if ok else f"Chrome: {message}")

    if focus_cursor:
        ok, message = windows.focus_app("cursor")
        emit(message if ok else f"Cursor: {message}")
        if ok and cursor_fullscreen and windows.IS_WINDOWS:
            time.sleep(0.6)
            window = windows.find_window(exe="Cursor.exe")
            if window is not None:
                windows.send_f11(window.hwnd)

    if delay > 0:
        time.sleep(delay)
    if phrase and speaker is not None:
        emit("Welcome line…")
        if not speaker.say(phrase, cache_key="welcome"):
            emit("The welcome line could not be spoken; check the ElevenLabs key.")
    elif not phrase:
        emit("No welcome phrase configured.")


__all__ = ["run_welcome_ritual"]
