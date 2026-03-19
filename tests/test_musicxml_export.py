from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from songscr.musicxml import export_musicxml


def test_export_musicxml_basic_fixture() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests" / "fixtures" / "musicxml_basic.songscr"

    xml_text = export_musicxml(fixture.read_text(encoding="utf-8"))
    xml_root = ET.fromstring(xml_text)

    measures = xml_root.findall(".//measure")
    harmonies = xml_root.findall(".//harmony")
    tie_starts = xml_root.findall(".//tie[@type='start']")
    tie_stops = xml_root.findall(".//tie[@type='stop']")

    assert xml_root.tag == "score-partwise"
    assert xml_root.find(".//time/beats") is not None
    assert xml_root.find(".//time/beats").text == "4"
    assert xml_root.find(".//time/beat-type") is not None
    assert xml_root.find(".//time/beat-type").text == "4"
    assert xml_root.find(".//sound") is not None
    assert xml_root.find(".//sound").get("tempo") == "96"
    assert len(measures) == 2
    assert harmonies
    assert tie_starts
    assert tie_stops


def test_export_musicxml_cli_smoke(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    input_file = root / "tests" / "fixtures" / "musicxml_basic.songscr"
    output_file = tmp_path / "musicxml_basic.musicxml"

    proc = subprocess.run(
        [sys.executable, "-m", "songscr", "export-musicxml", str(input_file), "-o", str(output_file)],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    xml_root = ET.fromstring(output_file.read_text(encoding="utf-8"))
    assert xml_root.tag == "score-partwise"


def test_export_musicxml_includes_lyrics_and_extend() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests" / "fixtures" / "lyrics_melisma.songscr"

    xml_text = export_musicxml(fixture.read_text(encoding="utf-8"))
    xml_root = ET.fromstring(xml_text)

    lyric_texts = xml_root.findall(".//lyric/text")
    lyric_extends = xml_root.findall(".//lyric/extend")

    assert lyric_texts
    assert any(node.text == "glo-" for node in lyric_texts)
    assert lyric_extends


def test_export_musicxml_writes_two_parts_for_bass_written_and_generated() -> None:
    root = Path(__file__).resolve().parents[1]
    written = root / "tests" / "fixtures" / "bass_written_basic.songscr"
    generated = root / "tests" / "fixtures" / "bass_pattern_root.songscr"

    written_root = ET.fromstring(export_musicxml(written.read_text(encoding="utf-8")))
    generated_root = ET.fromstring(export_musicxml(generated.read_text(encoding="utf-8")))

    assert len(written_root.findall("./part")) == 2
    assert len(generated_root.findall("./part")) == 2
