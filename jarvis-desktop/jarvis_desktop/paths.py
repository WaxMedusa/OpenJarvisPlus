"""Filesystem locations that work both from source and from a frozen build."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Jarvis"


def data_dir() -> Path:
    """Per-user writable directory for settings, secrets, cache and logs."""
    override = (os.environ.get("JARVIS_DATA_DIR") or "").strip()
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(root) / APP_NAME if root else Path.home() / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(root) / "jarvis"
    base.mkdir(parents=True, exist_ok=True)
    return base


def frontend_dir() -> Path:
    """Static UI directory, bundled as ``frontend/`` in the PyInstaller build."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "frontend"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "frontend"


def log_path() -> Path:
    return data_dir() / "jarvis.log"
