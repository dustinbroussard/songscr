from pathlib import Path

from songscr.core import lint_song


def test_pitch_bend_range_warns_for_wide_ramp() -> None:
    text = """[Tempo: 120] [Time Signature: 4/4]

[Verse]
[Track: Melody]
| C4 >> G4 | R | R | R |
"""
    issues = lint_song(text)
    warns = [i for i in issues if i.level == "WARN" and i.rule == "pitchBendRange"]
    assert warns


def test_pitch_bend_range_tag_suppresses_wide_ramp_warning() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "bend_range_global_12.songscr"
    issues = lint_song(fixture.read_text(encoding="utf-8"))
    warns = [i for i in issues if i.level == "WARN" and i.rule == "pitchBendRange"]
    assert not warns


def test_pitch_bend_range_out_of_range_is_error() -> None:
    text = """[Tempo: 120] [Pitch Bend Range: 0]

[Main]
[Track: Melody]
| C4 | R | R | R |
"""
    issues = lint_song(text)
    errs = [i for i in issues if i.level == "ERROR" and i.rule == "bendRange"]
    assert errs
