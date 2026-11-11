"""Install/remove a Windows Startup shortcut so Jarvis starts with the PC."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple

_SHORTCUT_NAME = "OpenJarvis JARVIS.lnk"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return (
            Path.home()
            / "AppData"
            / "Roaming"
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
    return (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )


def shortcut_path() -> Path:
    return _startup_dir() / _SHORTCUT_NAME


def is_startup_installed() -> bool:
    return shortcut_path().is_file()


def _pythonw() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        candidate = executable.with_name("pythonw.exe")
        if candidate.is_file():
            return str(candidate)
    return str(executable)


def _working_directory() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").is_file():
        return cwd
    # src/openjarvis/wake/startup.py -> project root for a source checkout.
    return Path(__file__).resolve().parents[3]


def install_startup(*, extra_args: str = "--ui") -> Tuple[bool, str]:
    """Create the Startup shortcut. Returns (ok, message)."""
    if os.name != "nt":
        return False, "Starting with Windows is only supported on Windows."
    target = shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lnk = str(target).replace("'", "''")
    exe = _pythonw().replace("'", "''")
    workdir = str(_working_directory()).replace("'", "''")
    arguments = f"-m openjarvis.cli wake {extra_args}".strip()
    script = (
        "$W=New-Object -ComObject WScript.Shell;"
        f"$S=$W.CreateShortcut('{lnk}');"
        f"$S.TargetPath='{exe}';"
        f"$S.Arguments='{arguments}';"
        f"$S.WorkingDirectory='{workdir}';"
        "$S.WindowStyle=7;"
        "$S.Save()"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not create the startup shortcut: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "PowerShell failed.").strip()[
            :300
        ]
    return True, f"Jarvis will start with Windows ({target})."


def uninstall_startup() -> Tuple[bool, str]:
    target = shortcut_path()
    if not target.exists():
        return True, "The startup shortcut was not present."
    try:
        target.unlink()
    except OSError as exc:
        return False, f"Could not remove the shortcut: {exc}"
    return True, "Jarvis will no longer start with Windows."


__all__ = [
    "install_startup",
    "is_startup_installed",
    "shortcut_path",
    "uninstall_startup",
]
