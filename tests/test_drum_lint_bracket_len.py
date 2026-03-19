from songscr.core import lint_song


def test_drum_bracket_len_mismatch_is_error() -> None:
    text = """[Tempo: 120] [Time Signature: 4/4] [Quantize: 16th]

[Main]
[Track: Drums]
| [H H H] | [K . S .] | [H H H H] | [. . C .] |
"""
    issues = lint_song(text)
    bracket_len_errors = [i for i in issues if i.level == "ERROR" and i.rule == "drumBracketLen"]
    assert bracket_len_errors
    assert bracket_len_errors[0].expected == "exactly 4 tokens"
