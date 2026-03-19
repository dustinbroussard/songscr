from pathlib import Path

from songscr.analyze import analyze_song


def test_analyze_playback_plan_reflects_repeats() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "struct_repeat_goto.songscr"
    analysis = analyze_song(fixture.read_text(encoding="utf-8"))

    assert analysis["global"]["playback_section_instance_count"] > analysis["global"]["section_count"]
