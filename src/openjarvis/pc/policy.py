"""Autonomy policy for machine-changing tool calls.

Three modes:

``ask``
    Read-only tools run freely, benign UI actions run freely, everything that
    installs, downloads or executes a shell command needs a confirmation.

``trusted``
    Same, but confirmations are auto-approved. Destructive shell patterns are
    still refused — "trusted" is not "unrestricted".

``read_only``
    Only reads and harmless window inspection are allowed. Useful for a first
    run to see what the operator would do.

``dry_run`` is an orthogonal switch: every state-changing call is denied with
a message describing what *would* have happened, so demos touch nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Literal, Optional

PolicyMode = Literal["ask", "trusted", "read_only"]

#: Tools that only observe the machine.
READ_ONLY_TOOLS = frozenset(
    {
        "calendar_today",
        "file_find",
        "screen_capture",
        "steam_status",
        "window_list",
        "winget_status",
        "think",
    }
)

#: Tools that change visible state but are cheap and reversible.
BENIGN_TOOLS = frozenset({"app_focus", "window_layout"})

#: Tools that launch something. Reversible, but still a side effect.
LAUNCH_TOOLS = frozenset({"app_open"})

#: Tools that mutate the system in ways that need consent.
SENSITIVE_TOOLS = frozenset({"pc_shell", "steam_update", "winget_upgrade"})

SIDE_EFFECT_TOOLS = BENIGN_TOOLS | LAUNCH_TOOLS | SENSITIVE_TOOLS

#: Commands that are refused even in ``trusted`` mode. These are the ones with
#: no sensible undo: disk wipes, boot-config edits, account changes, forced
#: shutdowns and recursive deletes outside a project.
DEFAULT_DENY_PATTERNS: tuple[str, ...] = (
    r"\bformat(?:\.com)?\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\bbootrec\b",
    r"\bshutdown(?:\.exe)?\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bremove-item\b[^\n]*-recurse",
    r"\bdel\s+/[sq]\b",
    r"\brd\s+/s\b",
    r"\brm\s+-rf\s+(?:/|~|[a-z]:\\)",
    r"\breg\s+delete\b",
    r"\bnet\s+user\b",
    r"\btakeown\b",
    r"\bicacls\b",
    r"\bcipher\s+/w\b",
    r"\bvssadmin\b",
    r"\bwbadmin\b",
    r"\bdisk\s+clean\b",
)


@dataclass(frozen=True)
class Decision:
    """Outcome of a policy check."""

    kind: Literal["allow", "ask", "deny"]
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.kind == "allow"


class AutonomyPolicy:
    """Decide whether a tool call may run, must be confirmed, or is refused."""

    def __init__(
        self,
        mode: PolicyMode = "ask",
        *,
        dry_run: bool = False,
        deny_patterns: Optional[Iterable[str]] = None,
        allow_shell: bool = True,
    ) -> None:
        self.mode: PolicyMode = mode
        self.dry_run = dry_run
        self.allow_shell = allow_shell
        patterns = tuple(deny_patterns or DEFAULT_DENY_PATTERNS)
        self._deny = tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _parse(arguments: Any) -> dict:
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _shell_denial(self, params: dict) -> Optional[str]:
        command = str(params.get("command", ""))
        for pattern in self._deny:
            if pattern.search(command):
                return (
                    "This command matches a protected pattern "
                    f"({pattern.pattern}) and is never run automatically."
                )
        return None

    # -- public API --------------------------------------------------------
    def decide(self, tool_name: str, arguments: Any = None) -> Decision:
        params = self._parse(arguments)

        if tool_name == "pc_shell":
            if not self.allow_shell:
                return Decision("deny", "Shell access is disabled by configuration.")
            denial = self._shell_denial(params)
            if denial:
                return Decision("deny", denial)

        if tool_name in READ_ONLY_TOOLS:
            return Decision("allow")

        if tool_name not in SIDE_EFFECT_TOOLS:
            # Unknown tools (MCP, skills) get the conservative treatment.
            if self.mode == "trusted":
                return Decision("allow")
            return Decision("deny", f"Tool {tool_name!r} is not on the PC allowlist.")

        if self.dry_run:
            return Decision(
                "deny",
                f"Dry run: {tool_name} would have run with {json.dumps(params)[:200]}",
            )

        if self.mode == "read_only":
            return Decision("deny", "Read-only mode: state-changing actions are off.")

        if tool_name in BENIGN_TOOLS:
            return Decision("allow")

        if tool_name in LAUNCH_TOOLS:
            # Opening an app is how the user asks for things to happen; it is
            # visible, reversible (close it) and cheap. No prompt.
            return Decision("allow")

        if self.mode == "trusted":
            return Decision("allow")

        return Decision("ask", f"{tool_name} changes this machine.")

    def describe(self) -> str:
        extras: List[str] = []
        if self.dry_run:
            extras.append("dry-run")
        return f"autonomy={self.mode}" + (f" ({', '.join(extras)})" if extras else "")


__all__ = [
    "AutonomyPolicy",
    "DEFAULT_DENY_PATTERNS",
    "Decision",
    "PolicyMode",
    "READ_ONLY_TOOLS",
    "SENSITIVE_TOOLS",
    "SIDE_EFFECT_TOOLS",
]
