from pathlib import Path
import subprocess
import sys

from songscr.core import lint_song


def test_lyrics_overflow_warns() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "lyrics_overflow.songscr"
    issues = lint_song(fixture.read_text(encoding="utf-8"))
    warns = [issue for issue in issues if issue.level == "WARN" and issue.rule == "lyricsOverflow"]
    assert warns


def test_lyrics_report_cli_smoke() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests" / "fixtures" / "lyrics_melisma.songscr"
    proc = subprocess.run(
        [sys.executable, "-m", "songscr", "lyrics-report", str(fixture)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Verse#1:" in proc.stdout
    assert "estimated_syllables=2" in proc.stdout


def test_bass_pattern_without_chords_warns() -> None:
    text = """[Tempo: 120] [Bass Pattern: Root]

[Verse]
[Track: Melody]
| C4 | R | R | R |
"""
    issues = lint_song(text)
    warns = [issue for issue in issues if issue.level == "WARN" and issue.rule == "bassPatternNoChords"]
    assert warns


def test_unknown_style_warns() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "template_unknown_style.songscr"
    issues = lint_song(fixture.read_text(encoding="utf-8"))
    warns = [issue for issue in issues if issue.level == "WARN" and issue.rule == "styleUnknown"]
    assert warns
