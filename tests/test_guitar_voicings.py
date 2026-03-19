from pathlib import Path

from songscr.chords import parse_chord_symbol
from songscr.core import lint_song
from songscr.guitar_voicings import STANDARD_GUITAR_TUNING, generate_guitar_voicing, parse_guitar_tuning


def test_guitar_bad_strings_lints_error() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "guitar_voicing_bad_strings.songscr"
    issues = lint_song(fixture.read_text(encoding="utf-8"))
    errors = [issue for issue in issues if issue.level == "ERROR" and issue.rule == "guitarStrings"]
    assert errors


def test_generate_guitar_voicing_is_ascending_and_capo_sensitive() -> None:
    tuning = parse_guitar_tuning("E2 A2 D3 G3 B3 E4")
    spec = parse_chord_symbol("G")
    assert spec is not None

    no_capo = generate_guitar_voicing(spec, tuning, capo=0, string_set=[6, 4, 3, 2], position_pref="Open")
    capo_three = generate_guitar_voicing(spec, tuning, capo=3, string_set=[6, 4, 3, 2], position_pref="Open")

    assert len(no_capo) == 4
    assert all(no_capo[idx] < no_capo[idx + 1] for idx in range(len(no_capo) - 1))
    assert len(capo_three) == 4
    assert all(capo_three[idx] < capo_three[idx + 1] for idx in range(len(capo_three) - 1))
    assert min(capo_three) > min(no_capo)


def test_parse_guitar_tuning_standard() -> None:
    assert parse_guitar_tuning("E2 A2 D3 G3 B3 E4") == STANDARD_GUITAR_TUNING
