from pathlib import Path
import subprocess
import sys


def test_chords_voicings_snapshot(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    input_file = root / "tests" / "fixtures" / "chords_basic.songscr"
    expected_file = root / "tests" / "fixtures" / "chords_basic.dump.txt"
    output_file = tmp_path / "chords_basic.dump.txt"

    proc = subprocess.run(
        [sys.executable, "-m", "songscr", "dump-midi", str(input_file), "-o", str(output_file)],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert output_file.read_text(encoding="utf-8") == expected_file.read_text(encoding="utf-8")
