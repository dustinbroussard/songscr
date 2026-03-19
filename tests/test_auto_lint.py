from pathlib import Path

from songscr.core import lint_song


def test_auto_malformed_is_error() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "auto_bad_syntax.songscr"
    text = fixture.read_text(encoding="utf-8")
    issues = lint_song(text)
    errs = [i for i in issues if i.level == "ERROR" and i.rule == "autoSyntax"]
    assert errs
    assert errs[0].section == "Verse"
    assert errs[0].track == "Chords"
    assert errs[0].bar == 1


def test_auto_unknown_param_is_warn() -> None:
    text = """[Tempo: 120] [Auto: SpaceWarp 0%->100%]

[Main]
[Track: Chords]
| C | C | C | C |
"""
    issues = lint_song(text)
    warns = [i for i in issues if i.level == "WARN" and i.rule == "autoParamUnknown"]
    assert warns
