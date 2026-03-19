from songscr.core import lint_song


def test_struct_loop_reports_error() -> None:
    text = """[Tempo: 120] [Time Signature: 4/4] [Quantize: 16th]

#Loop
[Goto: #Loop]

[Verse]
[Track: Chords]
| C | C | C | C |
"""
    issues = lint_song(text)
    loop_errors = [i for i in issues if i.level == "ERROR" and i.rule == "structLoop"]
    assert loop_errors
