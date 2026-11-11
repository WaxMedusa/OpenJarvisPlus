"""Append-only audit trail for every action the PC operator takes.

The operator can install software, start downloads and run shell commands, so
"what did it do while I was away?" has to be answerable after the fact. One
JSON object per line, flushed immediately, safe to call from several threads.

Secrets never belong in this file: values whose key looks like a credential
are replaced by ``"<redacted>"`` before the entry is serialised.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from openjarvis.core.paths import get_config_dir

_SENSITIVE_MARKERS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "cookie",
)
_MAX_DETAIL = 2_000


def redact(value: Any) -> Any:
    """Return *value* with credential-looking fields replaced."""
    if isinstance(value, Mapping):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE_MARKERS):
                cleaned[str(key)] = "<redacted>"
            else:
                cleaned[str(key)] = redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > _MAX_DETAIL:
        return value[:_MAX_DETAIL] + "…"
    return value


class AuditLog:
    """Thread-safe JSONL writer under ``~/.openjarvis/wake/audit.jsonl``."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self._path = (
            Path(path)
            if path is not None
            else get_config_dir() / "wake" / "audit.jsonl"
        )
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        action: str,
        *,
        arguments: Any = None,
        ok: bool = True,
        detail: str = "",
        approval: str = "auto",
    ) -> None:
        """Append one entry. Never raises: auditing must not break the action."""
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "epoch": round(time.time(), 3),
            "action": action,
            "arguments": redact(arguments if arguments is not None else {}),
            "ok": bool(ok),
            "approval": approval,
            "detail": redact(str(detail or "")),
        }
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(entry, ensure_ascii=False, default=str) + "\n"
                    )
        except OSError:
            # A read-only config dir must not take the operator down.
            pass

    def tail(self, limit: int = 50) -> Iterable[Dict[str, Any]]:
        """Return up to *limit* most recent entries, oldest first."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = []
        for line in lines[-max(1, limit) :]:
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries

    def describe_last(self, limit: int = 5) -> str:
        """Human-readable summary used by the ``/status`` command."""
        entries = list(self.tail(limit))
        if not entries:
            return "No actions recorded yet."
        return "\n".join(
            f"{item.get('ts', '?')}  {item.get('action', '?')}"
            f"  {'ok' if item.get('ok') else 'failed'}"
            for item in entries
        )


__all__ = ["AuditLog", "redact"]
