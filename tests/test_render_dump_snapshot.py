from pathlib import Path
import subprocess
import sys


def test_render_dump_snapshot(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    cases = [
        ("sample.songscr", "sample.dump.txt"),
        ("tests/fixtures/melody_grid_16ths.songscr", "melody_grid_16ths.dump.txt"),
        ("tests/fixtures/melody_brackets.songscr", "melody_brackets.dump.txt"),
        ("tests/fixtures/drums_grid_8ths.songscr", "drums_grid_8ths.dump.txt"),
        ("tests/fixtures/drums_grid_16ths.songscr", "drums_grid_16ths.dump.txt"),
        ("tests/fixtures/drums_brackets.songscr", "drums_brackets.dump.txt"),
        ("tests/fixtures/struct_repeat_goto.songscr", "struct_repeat_goto.dump.txt"),
        ("tests/fixtures/struct_alt_endings.songscr", "struct_alt_endings.dump.txt"),
        ("tests/fixtures/auto_section_reverb.songscr", "auto_section_reverb.dump.txt"),
        ("tests/fixtures/auto_track_cutoff.songscr", "auto_track_cutoff.dump.txt"),
        ("tests/fixtures/melody_bend.songscr", "melody_bend.dump.txt"),
        ("tests/fixtures/melody_ramp.songscr", "melody_ramp.dump.txt"),
        ("tests/fixtures/melody_vibrato.songscr", "melody_vibrato.dump.txt"),
        ("tests/fixtures/bend_range_global_12.songscr", "bend_range_global_12.dump.txt"),
        ("tests/fixtures/bend_range_section_override.songscr", "bend_range_section_override.dump.txt"),
        ("tests/fixtures/lyrics_basic.songscr", "lyrics_basic.dump.txt"),
        ("tests/fixtures/lyrics_melisma.songscr", "lyrics_melisma.dump.txt"),
        ("tests/fixtures/bass_written_basic.songscr", "bass_written_basic.dump.txt"),
        ("tests/fixtures/bass_pattern_root.songscr", "bass_pattern_root.dump.txt"),
        ("tests/fixtures/bass_pattern_root5_eighths.songscr", "bass_pattern_root5_eighths.dump.txt"),
        ("tests/fixtures/bass_pattern_walkup.songscr", "bass_pattern_walkup.dump.txt"),
        ("tests/fixtures/style_slowblues_fill.songscr", "style_slowblues_fill.dump.txt"),
        ("tests/fixtures/style_straightrock_fill.songscr", "style_straightrock_fill.dump.txt"),
        ("tests/fixtures/guitar_voicing_stringset_basic.songscr", "guitar_voicing_stringset_basic.dump.txt"),
        ("tests/fixtures/guitar_voicing_capo.songscr", "guitar_voicing_capo.dump.txt"),
        ("tests/fixtures/guitar_voicing_default_shape.songscr", "guitar_voicing_default_shape.dump.txt"),
    ]

    for input_rel, expected_rel in cases:
        input_file = root / input_rel
        expected_file = root / "tests" / "fixtures" / expected_rel
        output_file = tmp_path / expected_rel

        proc = subprocess.run(
            [sys.executable, "-m", "songscr", "dump-midi", str(input_file), "-o", str(output_file)],
            cwd=root,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        
        output_text = output_file.read_text(encoding="utf-8")
        expected_text = expected_file.read_text(encoding="utf-8")
        
        # Snapshot check
        assert output_text == expected_text, f"Snapshot mismatch for {expected_rel}"
        
        # Semantic checks
        lines = output_text.strip().splitlines()
        note_on_count = sum(1 for line in lines if "\tnote_on\t" in line)
        channels = set(line.split("\t")[3] for line in lines if len(line.split("\t")) > 3 and line.split("\t")[3].isdigit())
        
        # Invariants: 
        # 1. Number of note-on events should match number of note-off events
        note_off_count = sum(1 for line in lines if "\tnote_off\t" in line)
        assert note_on_count == note_off_count, f"Unbalanced note on/off in {expected_rel}"
        
        # 2. Events on the same tick shouldn't cause weird channel leaks
        for channel in channels:
            assert int(channel) in (0, 1, 2, 9), f"Unexpected channel {channel} utilized in {expected_rel}"
