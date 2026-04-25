from pathlib import Path

from songscr.io_utils import write_bytes_atomic, write_text_atomic


def test_write_text_atomic_replaces_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "song.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old", encoding="utf-8")

    write_text_atomic(target, "new text")

    assert target.read_text(encoding="utf-8") == "new text"
    assert not list(target.parent.glob(".*.tmp"))


def test_write_bytes_atomic_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "exports" / "song.mid"

    write_bytes_atomic(target, b"MThd")

    assert target.read_bytes() == b"MThd"
    assert not list(target.parent.glob(".*.tmp"))
