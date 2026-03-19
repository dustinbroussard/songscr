from pathlib import Path

from songscr.analyze import analyze_song


def test_analyze_reports_generated_tracks() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "style_slowblues_fill.songscr"
    analysis = analyze_song(fixture.read_text(encoding="utf-8"))

    assert analysis["global"]["templates_expanded_any_tracks"] is True
    assert analysis["global"]["bass_generated"] is True
    assert analysis["global"]["drums_generated"] is True
