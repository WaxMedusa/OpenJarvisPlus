from __future__ import annotations

from pathlib import Path

from openjarvis.pc.files import find_files


def test_find_files_matches_and_skips_noise(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "report.pdf").write_bytes(b"x" * 10)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "report.pdf").write_bytes(b"x")

    pdfs = find_files("*.pdf", root=tmp_path)
    assert [Path(item.path).name for item in pdfs] == ["report.pdf"]
    assert "node_modules" not in pdfs[0].path

    assert find_files("missing*", root=tmp_path) == []
    assert find_files("*", root=tmp_path / "nope") == []
