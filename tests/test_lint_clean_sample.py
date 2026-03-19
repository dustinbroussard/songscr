from pathlib import Path

from songscr.core import lint_song


def test_lint_sample_has_no_errors() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "sample.songscr").read_text(encoding="utf-8")

    issues = lint_song(text, filename="sample.songscr")

    assert not [issue for issue in issues if issue.level == "ERROR"]
