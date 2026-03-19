from pathlib import Path
import subprocess
import sys

from songscr.core import format_song


def test_expand_templates_materializes_missing_tracks(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    input_file = root / "tests" / "fixtures" / "style_slowblues_fill.songscr"
    output_file = tmp_path / "style_slowblues_fill.expanded.songscr"

    proc = subprocess.run(
        [sys.executable, "-m", "songscr", "expand-templates", str(input_file), "-o", str(output_file)],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    expanded = output_file.read_text(encoding="utf-8")
    assert "[Track: Drums]" in expanded
    assert "[Track: Bass]" in expanded
    assert format_song(expanded) == expanded


def test_expand_templates_preserves_explicit_drums(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    input_file = root / "tests" / "fixtures" / "style_funklite_explicit_drums.songscr"
    output_file = tmp_path / "style_funklite_explicit_drums.expanded.songscr"

    proc = subprocess.run(
        [sys.executable, "-m", "songscr", "expand-templates", str(input_file), "-o", str(output_file)],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    expanded = output_file.read_text(encoding="utf-8")
    assert expanded.count("[Track: Drums]") == 1
    assert expanded.count("[Track: Bass]") == 1
    assert "| K | S | K | S |" in expanded
