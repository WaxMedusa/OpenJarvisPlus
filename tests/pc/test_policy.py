from __future__ import annotations

from openjarvis.pc.policy import AutonomyPolicy


def test_read_only_tools_run_without_asking() -> None:
    policy = AutonomyPolicy("ask")
    assert policy.decide("steam_status").kind == "allow"
    assert policy.decide("window_list").kind == "allow"


def test_launching_an_app_is_allowed() -> None:
    policy = AutonomyPolicy("ask")
    assert policy.decide("app_open", {"target": "steam"}).kind == "allow"
    assert policy.decide("app_focus", {"app": "steam"}).kind == "allow"


def test_installs_ask_by_default_and_pass_when_trusted() -> None:
    asking = AutonomyPolicy("ask")
    assert asking.decide("steam_update").kind == "ask"
    assert asking.decide("winget_upgrade", {}).kind == "ask"
    trusted = AutonomyPolicy("trusted")
    assert trusted.decide("steam_update").kind == "allow"


def test_read_only_mode_blocks_changes() -> None:
    policy = AutonomyPolicy("read_only")
    assert policy.decide("app_open", {"target": "steam"}).kind == "deny"
    assert policy.decide("steam_update").kind == "deny"


def test_dry_run_blocks_side_effects_but_allows_reads() -> None:
    policy = AutonomyPolicy("ask", dry_run=True)
    assert policy.decide("steam_status").kind == "allow"
    decision = policy.decide("app_open", {"target": "steam"})
    assert decision.kind == "deny"
    assert "Dry run" in decision.reason


def test_destructive_shell_is_refused_even_when_trusted() -> None:
    policy = AutonomyPolicy("trusted")
    assert policy.decide("pc_shell", {"command": "format C: /q"}).kind == "deny"
    assert policy.decide("pc_shell", {"command": "shutdown /s /t 0"}).kind == "deny"
    assert policy.decide("pc_shell", {"command": "Get-Process"}).kind == "allow"


def test_shell_can_be_disabled() -> None:
    policy = AutonomyPolicy("trusted", allow_shell=False)
    assert policy.decide("pc_shell", {"command": "Get-Date"}).kind == "deny"
