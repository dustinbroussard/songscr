from pathlib import Path
import json
import subprocess
import sys


def test_analyze_json() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "sample.songscr"
    proc = subprocess.run(
        [sys.executable, "-m", "songscr", "analyze", str(fixture), "--format", "json"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    for key in ("global", "sections", "playback", "melody", "bass", "chords", "drums", "lyrics", "warnings"):
        assert key in payload
