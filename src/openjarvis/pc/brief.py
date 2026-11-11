"""The morning/arrival brief: what changed on this machine.

Deterministic on purpose. The brief is assembled from local files and one
package-manager query, so it works before any model has been contacted and
never invents an appointment. Anything that needs the model (calendar, mail)
is handled afterwards by the operator's own tools.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from openjarvis.pc import steam as steam_module
from openjarvis.pc import updates as updates_module
from openjarvis.pc.calendar import todays_events as calendar_events
from openjarvis.pc.windows import disk_usage


@dataclass
class Brief:
    speech: str = ""
    lines: List[str] = field(default_factory=list)
    pending_steam: int = 0
    pending_winget: int = 0
    steam_games: int = 0

    @property
    def empty(self) -> bool:
        return not self.lines


def _greeting(now: datetime) -> str:
    hour = now.hour
    if hour < 5:
        return "Working late"
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def collect_brief(
    config=None,
    *,
    include_winget: bool = True,
    winget_timeout: float = 15.0,
    now: Optional[datetime] = None,
) -> Brief:
    """Assemble a short spoken brief plus detailed lines for the terminal."""
    moment = now or datetime.now()
    pc_config = getattr(config, "pc", None)
    wake_config = getattr(config, "wake", None)
    honorific = (getattr(wake_config, "honorific", "") or "").strip() or "sir"
    root_override = getattr(pc_config, "steam_root", "") if pc_config else ""

    lines: List[str] = []
    spoken: List[str] = []

    lines.append(moment.strftime("%A, %d %B %Y — %H:%M"))
    spoken.append(f"{_greeting(moment)}, {honorific}.")

    # -- Steam -------------------------------------------------------------
    pending_steam = 0
    steam_games = 0
    try:
        status = steam_module.steam_status(root_override or "")
    except Exception as exc:  # never let one probe break the brief
        status = steam_module.SteamStatus(error=f"{type(exc).__name__}: {exc}")
    if not status.found:
        lines.append(f"Steam: {status.error or 'not found'}")
        spoken.append("I could not find Steam on this machine.")
    else:
        steam_games = len(status.games)
        pending = status.pending
        pending_steam = len(pending)
        if pending:
            names = ", ".join(game.name for game in pending[:4])
            more = "" if len(pending) <= 4 else f" and {len(pending) - 4} more"
            lines.append(
                f"Steam: {steam_games} games installed; {pending_steam} need updates "
                f"({names}{more})."
            )
            spoken.append(
                f"{pending_steam} of your {steam_games} Steam games need updating."
            )
        else:
            lines.append(
                f"Steam: {steam_games} games installed; everything is up to date."
            )
            spoken.append(f"All {steam_games} Steam games are up to date.")

    # -- winget ------------------------------------------------------------
    pending_winget = 0
    if include_winget and platform.system() == "Windows":
        if not updates_module.winget_available():
            lines.append("winget: not installed")
        else:
            packages, error = updates_module.list_upgrades(timeout=winget_timeout)
            if error and not packages:
                lines.append(f"winget: no answer ({error})")
            elif packages:
                pending_winget = len(packages)
                sample = ", ".join(pkg.name for pkg in packages[:3])
                lines.append(
                    f"winget: {pending_winget} update(s) available ({sample})."
                )
                spoken.append(f"{pending_winget} other apps have updates waiting.")
            else:
                lines.append("winget: everything is up to date.")
    elif include_winget:
        lines.append("winget: Windows only")

    # -- calendar ----------------------------------------------------------
    if getattr(wake_config, "include_calendar", True):
        calendar = calendar_events()
        if calendar.connected and calendar.events:
            lines.append("Calendar today: " + "; ".join(calendar.events[:6]))
            first_title = calendar.events[0].split("  ", 1)[-1]
            spoken.append(
                f"You have {len(calendar.events)} thing"
                f"{'s' if len(calendar.events) != 1 else ''} today, "
                f"starting with {first_title}."
            )
        elif calendar.connected:
            lines.append("Calendar today: nothing scheduled.")
            spoken.append("Your calendar is clear today.")
        elif calendar.error:
            lines.append(f"Calendar: {calendar.error}")

    # -- disk --------------------------------------------------------------
    free_gb, total_gb = disk_usage()
    if total_gb:
        lines.append(f"Disk: {free_gb:.0f} GB free of {total_gb:.0f} GB.")
        if free_gb < 10:
            spoken.append(f"Only {free_gb:.0f} gigabytes are free on this drive.")

    if pending_steam or pending_winget:
        spoken.append("Say the word and I will handle the updates.")
    else:
        spoken.append("Nothing needs attention right now.")

    return Brief(
        speech=" ".join(spoken),
        lines=lines,
        pending_steam=pending_steam,
        pending_winget=pending_winget,
        steam_games=steam_games,
    )


__all__ = ["Brief", "collect_brief"]
