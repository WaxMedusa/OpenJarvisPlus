from __future__ import annotations

from pathlib import Path

from openjarvis.pc.steam import library_paths, parse_vdf, steam_status


def test_parse_vdf_nested_and_comments() -> None:
    text = """
    // a comment
    "libraryfolders"
    {
        "0"
        {
            "path" "C:\\\\Program Files (x86)\\\\Steam"
            "apps" { "440" "123" }
        }
        "1"
        {
            "path" "D:\\\\SteamLibrary"
        }
    }
    """
    parsed = parse_vdf(text)
    folders = parsed["libraryfolders"]
    assert isinstance(folders, dict)
    assert folders["0"]["apps"]["440"] == "123"
    assert folders["1"]["path"] == "D:\\SteamLibrary"


def _write_manifest(
    root: Path, appid: str, name: str, flags: int, size: int = 1024
) -> None:
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    (steamapps / f"appmanifest_{appid}.acf").write_text(
        f'''
"AppState"
{{
    "appid" "{appid}"
    "name" "{name}"
    "StateFlags" "{flags}"
    "SizeOnDisk" "{size}"
    "installdir" "{name}"
}}
''',
        encoding="utf-8",
    )


def test_steam_status_detects_update_flags(tmp_path: Path) -> None:
    root = tmp_path / "Steam"
    _write_manifest(root, "10", "Updating", flags=2)
    _write_manifest(root, "20", "Queued", flags=8)
    _write_manifest(root, "30", "Current", flags=4)
    _write_manifest(root, "40", "Pinned", flags=4 | 256)

    status = steam_status(str(root))

    assert status.found
    assert len(status.games) == 4
    assert sorted(status.pending_ids()) == ["10", "20"]
    pinned = next(game for game in status.games if game.appid == "40")
    assert pinned.needs_update is False
    assert pinned.update_disabled is True


def test_library_paths_reads_libraryfolders(tmp_path: Path) -> None:
    root = tmp_path / "Steam"
    extra = tmp_path / "OtherLibrary"
    (root / "steamapps").mkdir(parents=True)
    (extra / "steamapps").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'''
"libraryfolders"
{{
    "0" {{ "path" "{root.as_posix()}" }}
    "1" {{ "path" "{extra.as_posix()}" }}
}}
''',
        encoding="utf-8",
    )

    paths = library_paths(root)

    assert paths[0] == root
    assert extra in paths


def test_missing_steam_returns_error(tmp_path: Path) -> None:
    status = steam_status(str(tmp_path / "nope"))
    assert status.found is False
    assert status.error
