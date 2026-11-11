"""Steam library inspection and update triggering.

Steam keeps the truth on disk: ``steamapps/libraryfolders.vdf`` lists every
library drive and each installed game has an ``appmanifest_<id>.acf`` with a
``StateFlags`` bitfield. That means "which games need updating?" can be answered
exactly, in milliseconds, without focusing a window — and after starting an
update we can poll the same bitfield to verify the download actually finished.

State flags (Valve's public values)::

    1   Uninstalled
    2   Update Required
    4   Fully Installed
    8   Update Queued
    16  Update Optional
    32  Files Missing
    64  Shared Only
    128 Disk Read Error
    256 Update Disabled

We treat ``2 | 8`` as "needs an update". A disabled or optional update is
reported separately so the operator never nags about a game the user pinned.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

STATE_UPDATE_REQUIRED = 2
STATE_UPDATE_QUEUED = 8
STATE_UPDATE_OPTIONAL = 16
STATE_UPDATE_DISABLED = 256

_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+')


# ---------------------------------------------------------------------------
# Minimal Valve KeyValues parser
# ---------------------------------------------------------------------------


def _unescape(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        token = token[1:-1]
    return (
        token.replace("\\\\", "\\")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def parse_vdf(text: str) -> Dict[str, object]:
    """Parse Valve KeyValues text into nested dicts.

    Handles quoted keys/values, nested blocks and ``//`` comments. Repeated keys
    keep the last value, which is what every consumer here wants.
    """
    tokens = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            newline = text.find("\n", index)
            index = length if newline == -1 else newline + 1
            continue
        if char in "{}":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            cursor = index + 1
            escaped = False
            while cursor < length:
                current = text[cursor]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
                cursor += 1
            tokens.append(text[index : cursor + 1])
            index = cursor + 1
            continue
        cursor = index
        while (
            cursor < length and not text[cursor].isspace() and text[cursor] not in '{}"'
        ):
            cursor += 1
        tokens.append(text[index:cursor])
        index = cursor

    position = 0

    def _parse_block() -> Dict[str, object]:
        nonlocal position
        block: Dict[str, object] = {}
        while position < len(tokens):
            token = tokens[position]
            if token == "}":
                position += 1
                return block
            if token == "{":
                # Orphan block; skip it rather than wedging the parser.
                position += 1
                _parse_block()
                continue
            position += 1
            key = _unescape(token)
            if position >= len(tokens):
                block[key] = ""
                return block
            value = tokens[position]
            if value == "{":
                position += 1
                block[key] = _parse_block()
                continue
            block[key] = _unescape(value)
            position += 1
        return block

    result = _parse_block()
    return result


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_steam_root(explicit: str = "") -> Optional[Path]:
    """Locate the Steam installation directory."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(os.path.expandvars(os.path.expanduser(explicit))))
    env_root = os.environ.get("STEAM_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
                if value:
                    candidates.append(Path(value))
        except OSError:
            pass
        for env_name, suffix in (
            ("ProgramFiles(x86)", r"Steam"),
            ("ProgramFiles", r"Steam"),
            ("LOCALAPPDATA", r"Steam"),
        ):
            base = os.environ.get(env_name)
            if base:
                candidates.append(Path(base) / suffix)
        candidates.append(Path(r"C:\Steam"))
    else:
        candidates.extend(
            [
                Path.home() / ".steam" / "steam",
                Path.home() / ".local" / "share" / "Steam",
                Path.home() / "Library" / "Application Support" / "Steam",
            ]
        )
    for candidate in candidates:
        if (candidate / "steamapps").is_dir() or (candidate / "Steam.exe").is_file():
            return candidate
    return None


def _normalise_library_path(raw: str, root: Path) -> Optional[Path]:
    value = os.path.expandvars(os.path.expanduser(str(raw)))
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path


def library_paths(root: Path) -> List[Path]:
    """Return every Steam library root, including *root* itself."""
    libraries: List[Path] = [root]
    vdf_path = root / "steamapps" / "libraryfolders.vdf"
    try:
        data = parse_vdf(vdf_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return libraries
    node = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    if isinstance(node, dict):
        for key, value in node.items():
            candidate: Optional[Path] = None
            if isinstance(value, dict):
                path_value = value.get("path")
                if isinstance(path_value, str) and path_value.strip():
                    candidate = _normalise_library_path(path_value, root)
            elif isinstance(value, str) and value.strip():
                # Old format: "1"  "D:\\SteamLibrary"
                candidate = _normalise_library_path(value, root)
            if candidate is not None and candidate not in libraries:
                libraries.append(candidate)
    return libraries


@dataclass(frozen=True)
class SteamGame:
    appid: str
    name: str
    install_dir: str
    size_bytes: int
    state_flags: int
    library: str

    @property
    def needs_update(self) -> bool:
        return bool(self.state_flags & (STATE_UPDATE_REQUIRED | STATE_UPDATE_QUEUED))

    @property
    def update_optional(self) -> bool:
        return bool(self.state_flags & STATE_UPDATE_OPTIONAL)

    @property
    def update_disabled(self) -> bool:
        return bool(self.state_flags & STATE_UPDATE_DISABLED)

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 1)


@dataclass
class SteamStatus:
    root: Optional[Path] = None
    libraries: List[Path] = field(default_factory=list)
    games: List[SteamGame] = field(default_factory=list)
    error: str = ""

    @property
    def found(self) -> bool:
        return self.root is not None

    @property
    def pending(self) -> List[SteamGame]:
        return [game for game in self.games if game.needs_update]

    def pending_ids(self) -> List[str]:
        return [game.appid for game in self.pending]


def _parse_manifest(path: Path, library: Path) -> Optional[SteamGame]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        data = parse_vdf(text)
    except OSError:
        return None
    state = data.get("AppState") or data.get("appstate") or {}
    if not isinstance(state, dict):
        return None
    appid = str(state.get("appid", "")).strip()
    name = str(state.get("name", "")).strip()
    if not appid:
        match = re.search(r"appmanifest_(\d+)\.acf", path.name)
        appid = match.group(1) if match else ""
    if not appid:
        return None
    try:
        flags = int(str(state.get("StateFlags", "0")).strip() or 0)
    except ValueError:
        flags = 0
    try:
        size = int(str(state.get("SizeOnDisk", "0")).strip() or 0)
    except ValueError:
        size = 0
    return SteamGame(
        appid=appid,
        name=name or f"App {appid}",
        install_dir=str(state.get("installdir", "")).strip(),
        size_bytes=size,
        state_flags=flags,
        library=str(library),
    )


def steam_status(root_override: str = "") -> SteamStatus:
    """Inspect every Steam library and report installed games."""
    root = find_steam_root(root_override)
    if root is None:
        return SteamStatus(error="Steam was not found on this machine.")
    libraries = library_paths(root)
    games: Dict[str, SteamGame] = {}
    for library in libraries:
        steamapps = library / "steamapps"
        if not steamapps.is_dir():
            continue
        for manifest in steamapps.glob("appmanifest_*.acf"):
            game = _parse_manifest(manifest, library)
            if game is not None:
                games[game.appid] = game
    return SteamStatus(
        root=root,
        libraries=[path for path in libraries if (path / "steamapps").is_dir()],
        games=sorted(games.values(), key=lambda item: item.name.lower()),
    )


def start_updates(games: Sequence[SteamGame]) -> tuple[bool, str]:
    """Ask the running Steam client to start downloading queued updates."""
    from openjarvis.pc.windows import open_target

    if not games:
        return True, "No Steam updates are pending."
    ok, message = open_target("steam://open/downloads")
    if not ok:
        return False, message
    names = ", ".join(game.name for game in games[:6])
    extra = "" if len(games) <= 6 else f" and {len(games) - 6} more"
    return True, (
        f"Opened Steam's downloads page for {len(games)} update(s): {names}{extra}. "
        "Steam will queue them if it is signed in."
    )


def wait_for_updates(
    pending_ids: Sequence[str],
    *,
    root_override: str = "",
    timeout_s: float = 5400,
    poll_s: float = 20,
    should_stop: Optional[Callable[[], bool]] = None,
    report: Optional[Callable[[str], None]] = None,
) -> str:
    """Poll manifests until the pending set empties (or we time out).

    Returns a human summary. This is the verification step: it never claims an
    update finished just because Steam was opened.
    """
    wanted = set(pending_ids)
    if not wanted:
        return "Nothing was pending."
    deadline = time.monotonic() + max(30.0, timeout_s)
    last_count = len(wanted)
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            return "Stopped watching before the updates finished."
        status = steam_status(root_override)
        remaining = [game for game in status.pending if game.appid in wanted]
        if not remaining:
            return f"All {len(wanted)} Steam update(s) finished."
        if report and len(remaining) != last_count:
            last_count = len(remaining)
            report(
                f"{len(remaining)} update(s) still running: "
                + ", ".join(game.name for game in remaining[:4])
            )
        time.sleep(max(5.0, poll_s))
    remaining = [
        game for game in steam_status(root_override).pending if game.appid in wanted
    ]
    if not remaining:
        return f"All {len(wanted)} Steam update(s) finished."
    return (
        f"{len(remaining)} update(s) are still not finished after "
        f"{int(timeout_s // 60)} minutes: "
        + ", ".join(game.name for game in remaining[:5])
    )


__all__ = [
    "SteamGame",
    "SteamStatus",
    "find_steam_root",
    "library_paths",
    "parse_vdf",
    "start_updates",
    "steam_status",
    "wait_for_updates",
]
