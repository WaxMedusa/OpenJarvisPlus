"""winget inspection and upgrades (Windows package manager).

``winget upgrade`` output is a human table, so parsing is deliberately
tolerant: it looks for the dashed separator, then splits each row on runs of
two or more spaces. Rows that do not fit are skipped rather than guessed at.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

_ROW_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<id>[^\s]+)\s+(?P<current>[^\s]+)\s+"
    r"(?P<available>[^\s]+)\s+(?P<source>[^\s]+)\s*$"
)
_SEPARATOR_RE = re.compile(r"^-{5,}")

_CACHE_TTL_S = 3600.0
_cache_lock = threading.Lock()
_cache: Optional[Tuple[float, List["WingetPackage"], str]] = None


@dataclass(frozen=True)
class WingetPackage:
    name: str
    package_id: str
    current: str
    available: str
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "id": self.package_id,
            "current": self.current,
            "available": self.available,
            "source": self.source,
        }


def winget_available() -> bool:
    return shutil.which("winget") is not None


def _run(args: List[str], timeout: float) -> Tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5.0, timeout),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def parse_upgrade_table(output: str) -> List[WingetPackage]:
    """Parse the table printed by ``winget upgrade``."""
    packages: List[WingetPackage] = []
    seen = set()
    in_table = False
    for raw_line in output.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if _SEPARATOR_RE.match(line.strip()):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.strip():
            continue
        match = _ROW_RE.match(line.strip())
        if not match:
            continue
        package = WingetPackage(
            name=match.group("name").strip(),
            package_id=match.group("id").strip(),
            current=match.group("current").strip(),
            available=match.group("available").strip(),
            source=match.group("source").strip(),
        )
        if package.package_id in seen:
            continue
        seen.add(package.package_id)
        packages.append(package)
    return packages


def list_upgrades(
    *, timeout: float = 30.0, use_cache: bool = True
) -> Tuple[List[WingetPackage], str]:
    """Return available upgrades plus an error string (empty on success)."""
    global _cache
    if not winget_available():
        return [], "winget is not installed."
    now = time.monotonic()
    with _cache_lock:
        if use_cache and _cache is not None and now - _cache[0] < _CACHE_TTL_S:
            return list(_cache[1]), _cache[2]
    code, output = _run(
        [
            "winget",
            "upgrade",
            "--include-unknown",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        timeout,
    )
    packages = parse_upgrade_table(output)
    error = ""
    if code != 0 and not packages:
        error = (output or f"winget exited with {code}").strip()[:300]
    with _cache_lock:
        _cache = (now, list(packages), error)
    return packages, error


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def upgrade(package_id: str = "", *, timeout: float = 900.0) -> Tuple[bool, str]:
    """Upgrade one package, or every upgradable package when *package_id* is empty."""
    if not winget_available():
        return False, "winget is not installed."
    args = [
        "winget",
        "upgrade",
        "--include-unknown",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    if package_id:
        args.extend(["--id", package_id, "--exact"])
    else:
        args.append("--all")
    code, output = _run(args, timeout)
    clear_cache()
    tail = "\n".join(output.strip().splitlines()[-6:])
    if code == 0:
        return True, tail or "Upgrade finished."
    # winget returns a non-zero code when a package is already current; surface
    # the output so the model can tell the difference between that and a failure.
    return False, tail or f"winget exited with {code}."


__all__ = [
    "WingetPackage",
    "clear_cache",
    "list_upgrades",
    "parse_upgrade_table",
    "upgrade",
    "winget_available",
]
