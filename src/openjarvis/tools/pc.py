"""PC operator tools: launch apps, inspect Steam/winget, run commands.

These tools are *not* registered in the global ``ToolRegistry`` on purpose.
They are wired directly into :class:`~openjarvis.agents.pc_operator.PCOperatorAgent`
by ``jarvis wake`` so a normal ``jarvis`` CLI run keeps its current tool surface
(and the security audit tables stay accurate). Use :func:`pc_tools` to obtain
instances.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.types import ToolResult
from openjarvis.pc import steam as steam_module
from openjarvis.pc import updates as updates_module
from openjarvis.pc.audit import AuditLog
from openjarvis.pc.calendar import todays_events as calendar_events
from openjarvis.pc.files import find_files
from openjarvis.pc.tasks import BackgroundTasks
from openjarvis.pc.windows import (
    IS_WINDOWS,
    find_window,
    focus_app,
    iter_windows,
    launch_app,
    move_window,
    run_powershell,
    screen_capture,
    screenshot_path,
)
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_SHELL_OUTPUT = 6000


def _ok(name: str, content: str, **metadata: Any) -> ToolResult:
    return ToolResult(tool_name=name, content=content, success=True, metadata=metadata)


def _fail(name: str, content: str, **metadata: Any) -> ToolResult:
    return ToolResult(tool_name=name, content=content, success=False, metadata=metadata)


class _PCTool(BaseTool):
    """Shared constructor state for operator tools."""

    category = "pc"

    def __init__(
        self,
        *,
        config: Any = None,
        board: Optional[BackgroundTasks] = None,
        audit: Optional[AuditLog] = None,
    ) -> None:
        self._config = config
        self._board = board
        self._audit = audit

    @property
    def _pc_config(self) -> Any:
        return getattr(self._config, "pc", None)


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


class PCShellTool(_PCTool):
    tool_id = "pc_shell"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="pc_shell",
            description=(
                "Run a PowerShell command on this Windows PC and return its output. "
                "Use for inspection (`Get-Process`, `Get-Service`) and for "
                "actions with "
                "no dedicated tool. Destructive patterns (disk format, shutdown, "
                "recursive deletes, account changes) are refused by policy."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "PowerShell command to run.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds to wait (default 90, max 300).",
                    },
                },
                "required": ["command"],
            },
            category="pc",
            requires_confirmation=True,
            required_capabilities=["code:execute"],
            timeout_seconds=310.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        command = str(params.get("command", "")).strip()
        if not command:
            return _fail(self.tool_id, "No command provided.")
        configured = getattr(self._pc_config, "shell_timeout_s", 90)
        try:
            timeout = int(params.get("timeout", configured) or configured)
        except (TypeError, ValueError):
            timeout = int(configured)
        timeout = max(5, min(timeout, 300))
        ok, output = run_powershell(command, timeout=timeout)
        output = output[:_MAX_SHELL_OUTPUT]
        return (
            _ok(self.tool_id, output, command=command)
            if ok
            else _fail(self.tool_id, output, command=command)
        )


# ---------------------------------------------------------------------------
# Apps and windows
# ---------------------------------------------------------------------------


class AppOpenTool(_PCTool):
    tool_id = "app_open"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="app_open",
            description=(
                "Open an application, file, folder, web page or protocol URI. "
                "Examples: 'steam', 'chrome', 'spotify', 'cursor', "
                "'https://calendar.google.com', 'steam://open/downloads', "
                "'C:/Users/me/Documents'. Returns an error when the app cannot be "
                "found."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "App name, absolute path, URL or URI.",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional arguments for an executable.",
                    },
                },
                "required": ["target"],
            },
            category="pc",
        )

    def execute(self, **params: Any) -> ToolResult:
        target = str(params.get("target", "")).strip()
        if not target:
            return _fail(self.tool_id, "Nothing to open.")
        ok, message = launch_app(target, str(params.get("args", "") or ""))
        return _ok(self.tool_id, message) if ok else _fail(self.tool_id, message)


class AppFocusTool(_PCTool):
    tool_id = "app_focus"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="app_focus",
            description=(
                "Bring an already-running app's window to the foreground, launching it "
                "if it is not running. Does not type or click."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Executable or known app name, e.g. 'steam'.",
                    },
                },
                "required": ["app"],
            },
            category="pc",
        )

    def execute(self, **params: Any) -> ToolResult:
        app = str(params.get("app", "")).strip()
        if not app:
            return _fail(self.tool_id, "No app provided.")
        ok, message = focus_app(app)
        return _ok(self.tool_id, message) if ok else _fail(self.tool_id, message)


class WindowListTool(_PCTool):
    tool_id = "window_list"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="window_list",
            description=(
                "List the visible top-level windows with their process name and screen "
                "rectangle. Use this to find out what is open before focusing "
                "or moving "
                "a window."
            ),
            parameters={"type": "object", "properties": {}},
            category="pc",
        )

    def execute(self, **params: Any) -> ToolResult:
        windows = iter_windows()
        if not windows:
            return _fail(self.tool_id, "No window list is available on this platform.")
        payload = [window.as_dict() for window in windows[:40]]
        return _ok(self.tool_id, json.dumps(payload, indent=2), count=len(payload))


class WindowLayoutTool(_PCTool):
    tool_id = "window_layout"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="window_layout",
            description=(
                "Move a window to a monitor. Identify it by title substring or process "
                "name. Monitors are numbered left-to-right starting at 1."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Window title substring or executable name.",
                    },
                    "monitor": {
                        "type": "integer",
                        "description": "1-based monitor number (default 1).",
                    },
                    "fullscreen": {
                        "type": "boolean",
                        "description": "Maximize on that monitor (default false).",
                    },
                    "width": {"type": "integer", "description": "Window width."},
                    "height": {"type": "integer", "description": "Window height."},
                },
                "required": ["target"],
            },
            category="pc",
        )

    def execute(self, **params: Any) -> ToolResult:
        target = str(params.get("target", "")).strip()
        if not target:
            return _fail(self.tool_id, "No window target provided.")
        window = find_window(title=target) or find_window(exe=target)
        if window is None:
            return _fail(self.tool_id, f"No open window matches {target!r}.")
        try:
            monitor = int(params.get("monitor", 1) or 1)
        except (TypeError, ValueError):
            monitor = 1
        try:
            width = int(params.get("width", 1400) or 1400)
            height = int(params.get("height", 900) or 900)
        except (TypeError, ValueError):
            width, height = 1400, 900
        ok, message = move_window(
            window.hwnd,
            monitor=monitor,
            fullscreen=bool(params.get("fullscreen", False)),
            width=width,
            height=height,
        )
        return _ok(self.tool_id, message) if ok else _fail(self.tool_id, message)


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------


def _steam_root_override(config: Any) -> str:
    return str(getattr(getattr(config, "pc", None), "steam_root", "") or "")


class SteamStatusTool(_PCTool):
    tool_id = "steam_status"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="steam_status",
            description=(
                "Read every Steam library on this machine and report installed games "
                "and which ones need updates. Reads manifest files, so it works "
                "whether "
                "or not Steam is running and takes well under a second. Always "
                "call this "
                "before steam_update."
            ),
            parameters={"type": "object", "properties": {}},
            category="pc",
        )

    def execute(self, **params: Any) -> ToolResult:
        status = steam_module.steam_status(_steam_root_override(self._config))
        if not status.found:
            return _fail(self.tool_id, status.error or "Steam was not found.")
        pending = status.pending
        lines = [
            f"Steam root: {status.root}",
            f"Libraries: {len(status.libraries)}",
            f"Installed games: {len(status.games)}",
            f"Updates pending: {len(pending)}",
        ]
        if pending:
            lines.append("")
            lines.append("Needs updating:")
            for game in pending[:25]:
                optional = " (optional)" if game.update_optional else ""
                lines.append(
                    f"  - {game.name} (appid {game.appid}, {game.size_gb} GB){optional}"
                )
        return _ok(
            self.tool_id,
            "\n".join(lines),
            pending=[game.appid for game in pending],
            installed=len(status.games),
        )


class SteamUpdateTool(_PCTool):
    tool_id = "steam_update"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="steam_update",
            description=(
                "Start pending Steam updates by opening Steam's downloads page, then "
                "watch the manifests in the background and report when they finish. "
                "Call steam_status first. The user's Steam client must be signed in."
            ),
            parameters={"type": "object", "properties": {}},
            category="pc",
            requires_confirmation=True,
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        root = _steam_root_override(self._config)
        status = steam_module.steam_status(root)
        if not status.found:
            return _fail(self.tool_id, status.error or "Steam was not found.")
        pending = status.pending
        if not pending:
            return _ok(self.tool_id, "No Steam updates are pending.")
        ok, message = steam_module.start_updates(pending)
        if not ok:
            return _fail(self.tool_id, message)
        watch_minutes = float(getattr(self._pc_config, "steam_watch_minutes", 90) or 90)
        if self._board is not None:
            started = self._board.start(
                "Steam updates",
                lambda report, should_stop: steam_module.wait_for_updates(
                    [game.appid for game in pending],
                    root_override=root,
                    timeout_s=watch_minutes * 60,
                    should_stop=should_stop,
                    report=report,
                ),
            )
            suffix = (
                " I'll keep watching and tell you when they finish."
                if started
                else " (A watch task was already running.)"
            )
        else:
            suffix = " I cannot watch it from this process, so check Steam in a while."
        return _ok(
            self.tool_id,
            message + suffix,
            pending=[game.appid for game in pending],
            background=self._board is not None,
        )


# ---------------------------------------------------------------------------
# winget
# ---------------------------------------------------------------------------


class WingetStatusTool(_PCTool):
    tool_id = "winget_status"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="winget_status",
            description=(
                "List applications with pending updates through Windows Package "
                "Manager (winget). Can take up to ~20 seconds on first call; results "
                "are cached for an hour."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "description": "Ignore the cache and query winget again.",
                    },
                },
            },
            category="pc",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        if not IS_WINDOWS:
            return _fail(self.tool_id, "winget is only available on Windows.")
        packages, error = updates_module.list_upgrades(
            use_cache=not bool(params.get("refresh", False))
        )
        if error and not packages:
            return _fail(self.tool_id, error)
        if not packages:
            return _ok(self.tool_id, "Everything is up to date through winget.")
        lines = [f"{len(packages)} update(s) available:"]
        for package in packages[:40]:
            lines.append(
                f"  - {package.name} [{package.package_id}] "
                f"{package.current} -> {package.available}"
            )
        return _ok(
            self.tool_id, "\n".join(lines), packages=[p.as_dict() for p in packages]
        )


class WingetUpgradeTool(_PCTool):
    tool_id = "winget_upgrade"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="winget_upgrade",
            description=(
                "Install available winget updates. Pass a package id for one app, or "
                "omit it to upgrade everything. Runs in the background when "
                "possible and "
                "reports progress."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "package_id": {
                        "type": "string",
                        "description": "Exact winget package id; omit to upgrade all.",
                    },
                },
            },
            category="pc",
            requires_confirmation=True,
            timeout_seconds=320.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        if not IS_WINDOWS:
            return _fail(self.tool_id, "winget is only available on Windows.")
        package_id = str(params.get("package_id", "") or "").strip()

        def _run(report, should_stop) -> Optional[str]:
            packages, error = updates_module.list_upgrades(use_cache=False)
            if error and not packages:
                raise RuntimeError(error)
            targets = (
                [package for package in packages if package.package_id == package_id]
                if package_id
                else packages
            )
            if not targets:
                return f"No matching package for {package_id!r}."
            results = []
            for index, package in enumerate(targets, start=1):
                if should_stop():
                    return "Stopped before finishing every package."
                report(f"Upgrading {package.name} ({index}/{len(targets)})…")
                ok, output = updates_module.upgrade(package.package_id)
                results.append(
                    (package.name, ok, output.splitlines()[-1] if output else "")
                )
            done = [name for name, ok, _ in results if ok]
            failed = [f"{name}: {detail}" for name, ok, detail in results if not ok]
            summary = f"{len(done)} of {len(targets)} packages upgraded."
            if failed:
                summary += " Failed: " + "; ".join(failed[:4])
            return summary

        if self._board is not None and not package_id:
            started = self._board.start("winget upgrades", _run)
            if started:
                return _ok(
                    self.tool_id,
                    "Started upgrading all available packages in the background. "
                    "I'll report when it finishes.",
                )
        summary = _run(lambda _message: None, lambda: False)
        return _ok(self.tool_id, summary or "Upgrade finished.")


# ---------------------------------------------------------------------------
# Screen / files
# ---------------------------------------------------------------------------


class CalendarTodayTool(_PCTool):
    tool_id = "calendar_today"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_today",
            description=(
                "Read today's events from the connected Google Calendar. Use this "
                "when the user asks what is on today. If the account is not "
                "connected it says so instead of guessing. To *open* the calendar "
                "page use app_open with https://calendar.google.com."
            ),
            parameters={"type": "object", "properties": {}},
            category="pc",
        )

    def execute(self, **params: Any) -> ToolResult:
        summary = calendar_events()
        if not summary.connected:
            return _fail(self.tool_id, summary.error or "Calendar is unavailable.")
        if not summary.events:
            return _ok(self.tool_id, "Nothing is scheduled on the calendar today.")
        lines = [f"{len(summary.events)} event(s) today:"]
        lines.extend(f"  - {event}" for event in summary.events)
        return _ok(self.tool_id, "\n".join(lines), events=summary.events)


class ScreenCaptureTool(_PCTool):
    tool_id = "screen_capture"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="screen_capture",
            description=(
                "Save a PNG screenshot of the primary screen to disk so the user can "
                "inspect it. This tool returns the file path; it cannot read the image "
                "contents itself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional output path; defaults under "
                            "~/.openjarvis/wake/screenshots."
                        ),
                    },
                },
            },
            category="pc",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        target = Path(str(params.get("path", "") or "") or screenshot_path())
        ok, message = screen_capture(target)
        return (
            _ok(self.tool_id, message, path=str(target))
            if ok
            else _fail(self.tool_id, message)
        )


class FileFindTool(_PCTool):
    tool_id = "file_find"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file_find",
            description=(
                "Search for files by name (glob patterns like '*.pdf' or 'invoice*') "
                "under the user's home directory or a given root. Returns the newest "
                "matches. Bounded to a few seconds."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Filename glob."},
                    "root": {
                        "type": "string",
                        "description": "Directory to search (default: home).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 40).",
                    },
                },
                "required": ["pattern"],
            },
            category="pc",
            timeout_seconds=20.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        pattern = str(params.get("pattern", "") or "").strip()
        if not pattern:
            return _fail(self.tool_id, "No pattern provided.")
        root_value = str(params.get("root", "") or "").strip()
        root = Path(os.path.expandvars(root_value)).expanduser() if root_value else None
        try:
            limit = int(params.get("limit", 40) or 40)
        except (TypeError, ValueError):
            limit = 40
        results = find_files(pattern, root=root, limit=max(1, min(limit, 100)))
        if not results:
            return _ok(self.tool_id, f"No files matching {pattern!r} were found.")
        lines = [f"{len(results)} match(es):"]
        for item in results:
            lines.append(f"  - {item.path} ({item.size_bytes / 1024:.0f} KB)")
        return _ok(
            self.tool_id, "\n".join(lines), paths=[item.path for item in results]
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def pc_tools(
    *,
    config: Any = None,
    board: Optional[BackgroundTasks] = None,
    audit: Optional[AuditLog] = None,
) -> List[BaseTool]:
    """Instantiate the operator toolset (not globally registered)."""
    return [
        PCShellTool(config=config, board=board, audit=audit),
        AppOpenTool(config=config, board=board, audit=audit),
        AppFocusTool(config=config, board=board, audit=audit),
        WindowListTool(config=config, board=board, audit=audit),
        WindowLayoutTool(config=config, board=board, audit=audit),
        SteamStatusTool(config=config, board=board, audit=audit),
        SteamUpdateTool(config=config, board=board, audit=audit),
        WingetStatusTool(config=config, board=board, audit=audit),
        WingetUpgradeTool(config=config, board=board, audit=audit),
        CalendarTodayTool(config=config, board=board, audit=audit),
        ScreenCaptureTool(config=config, board=board, audit=audit),
        FileFindTool(config=config, board=board, audit=audit),
    ]


__all__ = ["pc_tools"]
