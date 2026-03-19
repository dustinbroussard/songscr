from pathlib import Path

from songscr.analyze import analyze_song


def test_analyze_lyrics_metrics() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "lyrics_melisma.songscr"
    analysis = analyze_song(fixture.read_text(encoding="utf-8"))

    assert analysis["lyrics"]["total_lyric_tokens"] > 0
    assert analysis["lyrics"]["estimated_syllables"] > 0
