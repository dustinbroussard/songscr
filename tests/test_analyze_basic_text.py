from pathlib import Path
import subprocess
import sys


def test_analyze_basic_text() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "sample.songscr"
    proc = subprocess.run(
        [sys.executable, "-m", "songscr", "analyze", str(fixture)],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Global" in proc.stdout
    assert "Melody" in proc.stdout
    assert "Chords" in proc.stdout
    assert "Drums" in proc.stdout
