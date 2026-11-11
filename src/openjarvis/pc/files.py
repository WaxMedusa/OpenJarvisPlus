"""Bounded filesystem search used by the ``file_find`` tool."""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "$recycle.bin",
    "system volume information",
    "windows",
    "winsxs",
    "appdata",
    "library",
    "packages",
}


@dataclass(frozen=True)
class FoundFile:
    path: str
    size_bytes: int
    modified: float


def find_files(
    pattern: str,
    *,
    root: Optional[Path] = None,
    limit: int = 40,
    max_seconds: float = 8.0,
    max_depth: int = 7,
) -> List[FoundFile]:
    """Find files whose *name* matches a glob pattern under *root*.

    Bounded by time, depth and result count so a stray ``*`` cannot hang the
    operator. System and dependency directories are skipped.
    """
    needle = (pattern or "").strip() or "*"
    start = Path(root).expanduser() if root else Path.home()
    if not start.exists():
        return []
    deadline = time.monotonic() + max(0.5, max_seconds)
    results: List[FoundFile] = []

    def _walk(directory: Path, depth: int) -> None:
        if depth > max_depth or len(results) >= limit or time.monotonic() > deadline:
            return
        try:
            entries = list(os.scandir(directory))
        except (OSError, PermissionError):
            return
        for entry in entries:
            if len(results) >= limit or time.monotonic() > deadline:
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() in _SKIP_DIRS or entry.name.startswith("."):
                        continue
                    _walk(Path(entry.path), depth + 1)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if not fnmatch.fnmatch(entry.name.lower(), needle.lower()):
                    continue
                stat = entry.stat(follow_symlinks=False)
                results.append(
                    FoundFile(
                        path=entry.path,
                        size_bytes=stat.st_size,
                        modified=stat.st_mtime,
                    )
                )
            except OSError:
                continue

    _walk(start, 0)
    results.sort(key=lambda item: item.modified, reverse=True)
    return results[:limit]


__all__ = ["FoundFile", "find_files"]
