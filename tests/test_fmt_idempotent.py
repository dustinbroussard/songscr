from pathlib import Path

from songscr.core import format_song


def test_format_song_idempotent_sample() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "sample.songscr").read_text(encoding="utf-8")

    first = format_song(text)
    second = format_song(first)

    assert second == first
