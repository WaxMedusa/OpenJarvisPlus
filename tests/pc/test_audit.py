from __future__ import annotations

import json
from pathlib import Path

from openjarvis.pc.audit import AuditLog, redact


def test_redact_hides_credentials() -> None:
    cleaned = redact(
        {
            "command": "echo hi",
            "api_key": "sk-live-secret",
            "nested": {"password": "hunter2", "safe": "ok"},
        }
    )
    assert cleaned["api_key"] == "<redacted>"
    assert cleaned["nested"]["password"] == "<redacted>"
    assert cleaned["nested"]["safe"] == "ok"


def test_audit_log_appends_jsonl(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("app_open", arguments={"target": "steam", "token": "abc"}, ok=True)
    log.record("pc_shell", arguments={"command": "Get-Date"}, ok=False, detail="boom")

    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["action"] == "app_open"
    assert first["ok"] is True
    assert first["arguments"]["token"] == "<redacted>"
    second = json.loads(lines[1])
    assert second["ok"] is False
    assert "boom" in second["detail"]


def test_describe_last_summarises(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("steam_status", ok=True)
    summary = log.describe_last()
    assert "steam_status" in summary
    assert "ok" in summary
