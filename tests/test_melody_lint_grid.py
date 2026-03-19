from songscr.core import lint_song


def test_melody_grid_warning_expected_format() -> None:
    text = """[Tempo: 120] [Time Signature: 4/4] [Quantize: 16th]

[Main]
[Track: Melody]
| C4 | R | R | R | R |
"""
    issues = lint_song(text)
    grid_warnings = [i for i in issues if i.level == "WARN" and i.rule == "melodyGrid"]
    assert grid_warnings
    assert grid_warnings[0].expected == "4 or 16 cells (4/4, 16th)"


def test_melody_bracket_warnings_for_len_and_invalid_tokens() -> None:
    text = """[Tempo: 120] [Time Signature: 4/4] [Quantize: 16th]

[Main]
[Track: Melody]
| [C4 D4 E4 F4 G4 X1] | R | R | R |
"""
    issues = lint_song(text)
    warn_rules = {(i.rule, i.level) for i in issues}
    assert ("melodyBracketLen", "WARN") in warn_rules
    assert ("melodyBracket", "WARN") in warn_rules
