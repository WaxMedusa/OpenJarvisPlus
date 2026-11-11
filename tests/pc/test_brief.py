from __future__ import annotations

from datetime import datetime
from pathlib import Path

import openjarvis.pc.brief as brief_module
from openjarvis.pc.brief import collect_brief
from openjarvis.pc.calendar import CalendarSummary
from openjarvis.pc.steam import SteamGame, SteamStatus
from openjarvis.pc.updates import WingetPackage


class _Cfg:
    class pc:
        steam_root = ""

    class wake:
        honorific = "sir"


def _fake_status(*_args, **_kwargs) -> SteamStatus:
    games = [
        SteamGame(
            appid="10",
            name="Cyberpunk",
            install_dir="Cyberpunk",
            size_bytes=1024,
            state_flags=2,
            library="C:/Steam",
        ),
        SteamGame(
            appid="20",
            name="Hades",
            install_dir="Hades",
            size_bytes=1024,
            state_flags=4,
            library="C:/Steam",
        ),
    ]
    return SteamStatus(root=Path("C:/Steam"), games=games)


def test_brief_reports_pending_steam_updates(monkeypatch) -> None:
    monkeypatch.setattr(brief_module.steam_module, "steam_status", _fake_status)
    monkeypatch.setattr(
        brief_module, "calendar_events", lambda: CalendarSummary(connected=False)
    )
    monkeypatch.setattr(brief_module, "disk_usage", lambda: (120.0, 500.0))
    monkeypatch.setattr(brief_module.platform, "system", lambda: "Linux")

    brief = collect_brief(_Cfg(), include_winget=True, now=datetime(2026, 11, 11, 9, 0))

    assert brief.pending_steam == 1
    assert brief.steam_games == 2
    assert "Steam" in "\n".join(brief.lines)
    assert "need updating" in brief.speech
    assert "handle the updates" in brief.speech


def test_brief_reports_winget_and_disk(monkeypatch) -> None:
    monkeypatch.setattr(
        brief_module, "calendar_events", lambda: CalendarSummary(connected=False)
    )
    monkeypatch.setattr(
        brief_module.steam_module,
        "steam_status",
        lambda *_a, **_k: SteamStatus(error="not installed"),
    )
    monkeypatch.setattr(brief_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(brief_module.updates_module, "winget_available", lambda: True)
    monkeypatch.setattr(
        brief_module.updates_module,
        "list_upgrades",
        lambda **_kwargs: (
            [
                WingetPackage(
                    name="Git", package_id="Git.Git", current="1", available="2"
                )
            ],
            "",
        ),
    )
    monkeypatch.setattr(brief_module, "disk_usage", lambda: (7.0, 500.0))

    brief = collect_brief(_Cfg(), include_winget=True)

    assert brief.pending_winget == 1
    assert any("winget" in line for line in brief.lines)
    assert "Only 7 gigabytes" in brief.speech
    assert "not find Steam" in brief.speech
