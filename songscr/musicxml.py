from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import re
import xml.etree.ElementTree as ET

from .chords import ChordSpec, parse_chord_symbol
from .core import (
    _collect_timed_bass_events_for_bar,
    _parse_bracket_group,
    _parse_melody_event_tokens,
    _parse_quantize,
    _parse_tempo,
    _parse_time_signature,
    _strip_paren_dyn,
    _token_is_bracket_group,
    _tokens_for_take,
    build_lyrics_alignment_report,
    lint_song,
    parse_song,
    resolve_bass_octave,
    resolve_bass_pattern,
    resolve_bass_rhythm,
    _melody_grid_slots_per_bar,
    _melody_grid_mode,
)
from .bass import generate_bass_events_from_chords
from .styles import expand_song_templates
from .struct import build_playback_plan

_ALT_ENDING_RE = re.compile(r"^\{(\d+)\}$")
_ROOT_RE = re.compile(r"^([A-G])([#b]?)")


@dataclass
class MelodySpan:
    start: int
    end: int
    midi_note: int
    lyric_text: Optional[str] = None
    lyric_extend: bool = False


@dataclass
class HarmonyEvent:
    offset: int
    raw_symbol: str


@dataclass
class BassSpan:
    start: int
    end: int
    midi_note: int




def _cells_for_take(cells, take_number: int):
    return [_CellView(tokens=_tokens_for_take(cell.tokens, take_number)) for cell in cells]


@dataclass
class _CellView:
    tokens: List[str]


def _melody_cell_step_divisions(
    mode: str,
    divisions: int,
    grid_unit_divisions: int,
    measure_duration: int,
    cell_count: int,
) -> float:
    if mode == "beat":
        return float(divisions)
    if mode == "quantize":
        return float(grid_unit_divisions)
    if cell_count <= 0:
        return float(measure_duration)
    return measure_duration / cell_count


def _midi_to_pitch(midi_note: int) -> Tuple[str, Optional[int], int]:
    pitch_class = midi_note % 12
    octave = (midi_note // 12) - 1
    table = {
        0: ("C", None),
        1: ("C", 1),
        2: ("D", None),
        3: ("E", -1),
        4: ("E", None),
        5: ("F", None),
        6: ("F", 1),
        7: ("G", None),
        8: ("A", -1),
        9: ("A", None),
        10: ("B", -1),
        11: ("B", None),
    }
    step, alter = table[pitch_class]
    return step, alter, octave


def _kind_name(raw_symbol: str, spec: Optional[ChordSpec]) -> Tuple[str, str]:
    if spec is None:
        return "other", raw_symbol

    lower = raw_symbol.strip().lower()
    if lower.startswith(f"{spec.root.lower()}m7b5") or lower.startswith(f"{spec.root.lower()}min7b5"):
        return "half-diminished", raw_symbol
    if spec.sus == 2:
        return "suspended-second", raw_symbol
    if spec.sus == 4:
        return "suspended-fourth", raw_symbol
    if spec.quality == "aug":
        return "augmented", raw_symbol
    if spec.quality == "dim":
        return "diminished", raw_symbol
    if spec.quality == "min" and 7 in spec.extensions:
        return "minor-seventh", raw_symbol
    if spec.quality == "min":
        return "minor", raw_symbol
    if "maj7" in spec.alterations:
        return "major-seventh", raw_symbol
    if 7 in spec.extensions:
        return "dominant", raw_symbol
    return "major", raw_symbol


def _add_degree(parent: ET.Element, value: int, alter: int, degree_type: str) -> None:
    degree = ET.SubElement(parent, "degree")
    ET.SubElement(degree, "degree-value").text = str(value)
    ET.SubElement(degree, "degree-alter").text = str(alter)
    ET.SubElement(degree, "degree-type").text = degree_type


def _append_harmony(measure_el: ET.Element, harmony_event: HarmonyEvent) -> None:
    symbol = harmony_event.raw_symbol
    spec = parse_chord_symbol(symbol)
    root_match = _ROOT_RE.match(symbol)
    if root_match is None:
        return

    harmony_el = ET.SubElement(measure_el, "harmony")
    if harmony_event.offset > 0:
        offset_el = ET.SubElement(harmony_el, "offset")
        offset_el.set("sound", "no")
        offset_el.text = str(harmony_event.offset)

    root_el = ET.SubElement(harmony_el, "root")
    ET.SubElement(root_el, "root-step").text = root_match.group(1)
    if root_match.group(2) == "#":
        ET.SubElement(root_el, "root-alter").text = "1"
    elif root_match.group(2) == "b":
        ET.SubElement(root_el, "root-alter").text = "-1"

    kind_value, kind_text = _kind_name(symbol, spec)
    kind_el = ET.SubElement(harmony_el, "kind")
    kind_el.set("text", kind_text)
    kind_el.text = kind_value

    if spec is None:
        return

    for alteration in spec.alterations:
        if alteration == "b5":
            _add_degree(harmony_el, 5, -1, "alter")
        elif alteration == "#5":
            _add_degree(harmony_el, 5, 1, "alter")
        elif alteration == "b9":
            _add_degree(harmony_el, 9, -1, "alter")
        elif alteration == "#9":
            _add_degree(harmony_el, 9, 1, "alter")
    for value in sorted(spec.add):
        _add_degree(harmony_el, value, 0, "add")
    for value in sorted(v for v in spec.extensions if v in (9, 11, 13)):
        _add_degree(harmony_el, value, 0, "add")

    if spec.slash_bass:
        bass_match = _ROOT_RE.match(spec.slash_bass)
        if bass_match is not None:
            bass_el = ET.SubElement(harmony_el, "bass")
            ET.SubElement(bass_el, "bass-step").text = bass_match.group(1)
            if bass_match.group(2) == "#":
                ET.SubElement(bass_el, "bass-alter").text = "1"
            elif bass_match.group(2) == "b":
                ET.SubElement(bass_el, "bass-alter").text = "-1"


def _append_note(
    measure_el: ET.Element,
    *,
    duration: int,
    midi_note: Optional[int],
    tie_stop: bool,
    tie_start: bool,
    lyric_text: Optional[str] = None,
    lyric_extend: bool = False,
) -> None:
    note_el = ET.SubElement(measure_el, "note")
    if midi_note is None:
        ET.SubElement(note_el, "rest")
    else:
        step, alter, octave = _midi_to_pitch(midi_note)
        pitch_el = ET.SubElement(note_el, "pitch")
        ET.SubElement(pitch_el, "step").text = step
        if alter is not None:
            ET.SubElement(pitch_el, "alter").text = str(alter)
        ET.SubElement(pitch_el, "octave").text = str(octave)
        if tie_stop:
            tie_el = ET.SubElement(note_el, "tie")
            tie_el.set("type", "stop")
        if tie_start:
            tie_el = ET.SubElement(note_el, "tie")
            tie_el.set("type", "start")

    ET.SubElement(note_el, "duration").text = str(duration)
    ET.SubElement(note_el, "voice").text = "1"

    if midi_note is not None and (tie_stop or tie_start):
        notations_el = ET.SubElement(note_el, "notations")
        if tie_stop:
            tied_el = ET.SubElement(notations_el, "tied")
            tied_el.set("type", "stop")
        if tie_start:
            tied_el = ET.SubElement(notations_el, "tied")
            tied_el.set("type", "start")

    if midi_note is not None and (lyric_text is not None or lyric_extend):
        lyric_el = ET.SubElement(note_el, "lyric")
        if lyric_extend:
            ET.SubElement(lyric_el, "extend")
        else:
            ET.SubElement(lyric_el, "syllabic").text = "single"
            ET.SubElement(lyric_el, "text").text = lyric_text


def _collect_flattened_content(text: str):
    song = expand_song_templates(parse_song(text))
    issues = lint_song(text)
    errors = [issue for issue in issues if issue.level == "ERROR"]
    if errors:
        msgs = "\n".join(issue.format_line() for issue in errors)
        raise ValueError(f"Lint failed:\n{msgs}")

    playback_plan, struct_issues = build_playback_plan(song)
    struct_errors = [issue for issue in struct_issues if issue.level == "ERROR"]
    if struct_errors:
        msgs = "\n".join(f'ERROR rule="{issue.rule}" {issue.message}' for issue in struct_errors)
        raise ValueError(f"Struct planning failed:\n{msgs}")

    num, den = _parse_time_signature(song)
    bpm = _parse_tempo(song)
    quantize = _parse_quantize(song)
    divisions = 4 if quantize == 16 else 2
    beats_per_bar = int(round(num * (4 / den)))
    measure_duration = beats_per_bar * divisions
    grid_unit_divisions = max(1, divisions * 4 // quantize)

    measures: List[Dict[str, object]] = []
    melody_spans: List[MelodySpan] = []
    bass_spans: List[BassSpan] = []
    lyric_schedule: Dict[int, List[Tuple[Optional[str], bool]]] = {}
    has_melody_track = False
    has_chords_track = False
    has_bass_track = False
    abs_cursor = 0

    for section_report in build_lyrics_alignment_report(song):
        for bar_report in section_report.bars:
            for attachment in bar_report.attachments:
                lyric_start = int(round(attachment.note_start * (divisions / 480)))
                lyric_schedule.setdefault(lyric_start, []).append((attachment.text, attachment.extend))

    for sec_inst in playback_plan:
        section = song.sections[sec_inst.section_index]
        tracks = section.tracks
        max_bars = 0
        for track in tracks.values():
            max_bars = max(max_bars, len(track.bars))

        melody_track = tracks.get("Melody")
        bass_track = tracks.get("Bass")
        chords_track = tracks.get("Chords") or tracks.get("Chord")
        if melody_track is not None:
            has_melody_track = True
        if bass_track is not None:
            has_bass_track = True
        if chords_track is not None:
            has_chords_track = True

        explicit_bass_present = False
        abs_cursor_ticks = int(round(abs_cursor * (480 / divisions)))
        bar_ticks = int(round(measure_duration * (480 / divisions)))
        beat_ticks = int(round(divisions * (480 / divisions)))
        if bass_track is not None:
            for bar_index, bar in enumerate(bass_track.bars):
                filtered_cells = _cells_for_take(bar.cells, sec_inst.take_number)
                bass_events, _ = _collect_timed_bass_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    480,
                    bar_ticks,
                    max(1, int(round(grid_unit_divisions * (480 / divisions)))),
                    abs_cursor_ticks + (bar_index * bar_ticks),
                    section_name=section.name,
                    section_instance=sec_inst.instance_number,
                    bar_index=bar.index,
                )
                for event in bass_events:
                    explicit_bass_present = True
                    bass_spans.append(
                        BassSpan(
                            start=int(round(event.start * (divisions / 480))),
                            end=int(round((event.start + event.duration) * (divisions / 480))),
                            midi_note=event.midi_note,
                        )
                    )
        if not explicit_bass_present:
            pattern = resolve_bass_pattern(song, sec_inst, bass_track)
            if pattern is not None and chords_track is not None:
                generated_events = generate_bass_events_from_chords(
                    section,
                    pattern,
                    resolve_bass_rhythm(song, sec_inst, bass_track),
                    resolve_bass_octave(song, sec_inst, bass_track),
                    {
                        "take_number": sec_inst.take_number,
                        "abs_cursor": abs_cursor_ticks,
                        "bar_duration": bar_ticks,
                        "beat_duration": beat_ticks,
                        "beats_per_bar": beats_per_bar,
                        "max_bars": max_bars,
                    },
                )
                for event in generated_events:
                    bass_spans.append(
                        BassSpan(
                            start=int(round(event.start * (divisions / 480))),
                            end=int(round((event.start + event.duration) * (divisions / 480))),
                            midi_note=event.midi_note,
                        )
                    )

        for bar_index in range(max_bars):
            measure_start = abs_cursor + (bar_index * measure_duration)
            measure_info: Dict[str, object] = {
                "number": len(measures) + 1,
                "harmonies": [],
            }

            if chords_track is not None and bar_index < len(chords_track.bars):
                bar = chords_track.bars[bar_index]
                cells = _cells_for_take(bar.cells, sec_inst.take_number)
                for beat_index, cell in enumerate(cells):
                    if beat_index >= beats_per_bar:
                        break
                    if not cell.tokens:
                        continue
                    token = _strip_paren_dyn(cell.tokens[0])
                    token = re.sub(r"\{[0-9\-]+\}$", "", token)
                    if token in ("%", "R", "(R)", ""):
                        continue
                    harmony_offset = beat_index * divisions
                    measure_info["harmonies"].append(HarmonyEvent(offset=harmony_offset, raw_symbol=token))

            if melody_track is not None and bar_index < len(melody_track.bars):
                bar = melody_track.bars[bar_index]
                cells = _cells_for_take(bar.cells, sec_inst.take_number)
                cell_count = len(cells)
                mode = _melody_grid_mode(cell_count, beats_per_bar, _melody_grid_slots_per_bar(beats_per_bar, quantize))
                cell_step = _melody_cell_step_divisions(mode, divisions, grid_unit_divisions, measure_duration, cell_count)

                def add_note_events(inner_tokens: List[str], start_base: int, inner_step: float) -> None:
                    idx = 0
                    while idx < len(inner_tokens):
                        spec, consumed = _parse_melody_event_tokens(inner_tokens, idx, 90)
                        consumed = max(1, consumed)
                        if spec is not None:
                            start = start_base + int(round(idx * inner_step))
                            slot_duration = max(1, int(round(inner_step)))
                            duration = slot_duration + (spec.dashes * grid_unit_divisions)
                            lyric_items = lyric_schedule.get(start, [])
                            lyric_text = None
                            lyric_extend = False
                            if lyric_items:
                                lyric_text, lyric_extend = lyric_items.pop(0)
                            melody_spans.append(MelodySpan(start=start, end=start + duration, midi_note=spec.midi_note, lyric_text=lyric_text, lyric_extend=lyric_extend))
                        idx += consumed

                for cell_index, cell in enumerate(cells):
                    tokens = cell.tokens
                    if not tokens:
                        continue
                    cell_start = measure_start + int(round(cell_index * cell_step))

                    if mode == "beat":
                        bracket_tokens = [tok for tok in tokens if _token_is_bracket_group(tok)]
                        if bracket_tokens:
                            for token in bracket_tokens:
                                inner_tokens = _parse_bracket_group(token)
                                if inner_tokens:
                                    inner_step = divisions / len(inner_tokens)
                                    add_note_events(inner_tokens, cell_start, inner_step)
                            continue

                    sub_count = max(1, len(tokens))
                    sub_step = cell_step / sub_count
                    idx = 0
                    while idx < len(tokens):
                        token = tokens[idx]
                        slot_start = cell_start + int(round(idx * sub_step))
                        if _token_is_bracket_group(token):
                            inner_tokens = _parse_bracket_group(token)
                            if inner_tokens:
                                inner_step = sub_step / len(inner_tokens)
                                add_note_events(inner_tokens, slot_start, inner_step)
                            idx += 1
                            continue
                        spec, consumed = _parse_melody_event_tokens(tokens, idx, 90)
                        consumed = max(1, consumed)
                        if spec is not None:
                            slot_duration = max(1, int(round(sub_step)))
                            duration = slot_duration + (spec.dashes * grid_unit_divisions)
                            lyric_items = lyric_schedule.get(slot_start, [])
                            lyric_text = None
                            lyric_extend = False
                            if lyric_items:
                                lyric_text, lyric_extend = lyric_items.pop(0)
                            melody_spans.append(MelodySpan(start=slot_start, end=slot_start + duration, midi_note=spec.midi_note, lyric_text=lyric_text, lyric_extend=lyric_extend))
                        idx += consumed

            measures.append(measure_info)

        abs_cursor += max_bars * measure_duration

    melody_spans.sort(key=lambda span: (span.start, span.end, span.midi_note))
    return {
        "num": num,
        "den": den,
        "bpm": bpm,
        "quantize": quantize,
        "divisions": divisions,
        "measure_duration": measure_duration,
        "measures": measures,
        "melody_spans": melody_spans,
        "bass_spans": bass_spans,
        "has_melody_track": has_melody_track,
        "has_bass_track": has_bass_track,
        "has_chords_track": has_chords_track,
    }


def export_musicxml_warnings(text: str) -> List[str]:
    content = _collect_flattened_content(text)
    warnings: List[str] = []
    if not content["has_melody_track"]:
        warnings.append("No Melody track found; exporting chords only.")
    if not content["has_chords_track"]:
        warnings.append("No Chords track found; exporting melody only.")
    return warnings


def export_musicxml(text: str) -> str:
    content = _collect_flattened_content(text)
    num = int(content["num"])
    den = int(content["den"])
    bpm = int(content["bpm"])
    divisions = int(content["divisions"])
    measure_duration = int(content["measure_duration"])
    measures = list(content["measures"])
    melody_spans: List[MelodySpan] = list(content["melody_spans"])
    bass_spans: List[BassSpan] = list(content["bass_spans"])

    score = ET.Element("score-partwise")
    score.set("version", "4.0")

    part_list = ET.SubElement(score, "part-list")
    score_part = ET.SubElement(part_list, "score-part")
    score_part.set("id", "P1")
    ET.SubElement(score_part, "part-name").text = "Melody"
    if bass_spans:
        bass_part = ET.SubElement(part_list, "score-part")
        bass_part.set("id", "P2")
        ET.SubElement(bass_part, "part-name").text = "Bass"

    def append_part(part_id: str, spans, include_harmony: bool) -> None:
        part = ET.SubElement(score, "part")
        part.set("id", part_id)

        span_index = 0
        position = 0
        for measure_info in measures:
            measure_number = int(measure_info["number"])
            measure_start = (measure_number - 1) * measure_duration
            measure_end = measure_start + measure_duration

            measure_el = ET.SubElement(part, "measure")
            measure_el.set("number", str(measure_number))

            if measure_number == 1:
                attributes = ET.SubElement(measure_el, "attributes")
                ET.SubElement(attributes, "divisions").text = str(divisions)
                key_el = ET.SubElement(attributes, "key")
                ET.SubElement(key_el, "fifths").text = "0"
                time_el = ET.SubElement(attributes, "time")
                ET.SubElement(time_el, "beats").text = str(num)
                ET.SubElement(time_el, "beat-type").text = str(den)
                clef_el = ET.SubElement(attributes, "clef")
                ET.SubElement(clef_el, "sign").text = "G"
                ET.SubElement(clef_el, "line").text = "2"

                if part_id == "P1":
                    direction = ET.SubElement(measure_el, "direction")
                    direction_type = ET.SubElement(direction, "direction-type")
                    metronome = ET.SubElement(direction_type, "metronome")
                    ET.SubElement(metronome, "beat-unit").text = "quarter"
                    ET.SubElement(metronome, "per-minute").text = str(bpm)
                    sound = ET.SubElement(direction, "sound")
                    sound.set("tempo", str(bpm))

            if include_harmony:
                for harmony_event in measure_info["harmonies"]:
                    _append_harmony(measure_el, harmony_event)

            if position < measure_start:
                position = measure_start

            while span_index < len(spans) and spans[span_index].end <= position:
                span_index += 1

            local_index = span_index
            while local_index < len(spans) and spans[local_index].start < measure_end:
                span = spans[local_index]
                segment_start = max(position, measure_start, span.start)
                if segment_start > position:
                    _append_note(measure_el, duration=segment_start - position, midi_note=None, tie_stop=False, tie_start=False)
                    position = segment_start
                if span.end <= position:
                    local_index += 1
                    continue
                segment_end = min(span.end, measure_end)
                if segment_end > position:
                    _append_note(
                        measure_el,
                        duration=segment_end - position,
                        midi_note=span.midi_note,
                        tie_stop=position > span.start,
                        tie_start=segment_end < span.end,
                        lyric_text=span.lyric_text if include_harmony and position == span.start else None,
                        lyric_extend=span.lyric_extend if include_harmony and position == span.start else False,
                    )
                    position = segment_end
                if span.end <= position:
                    local_index += 1
                else:
                    break

            if position < measure_end:
                _append_note(measure_el, duration=measure_end - position, midi_note=None, tie_stop=False, tie_start=False)
                position = measure_end

            while span_index < len(spans) and spans[span_index].end <= position:
                span_index += 1

    append_part("P1", melody_spans, include_harmony=True)
    if bass_spans:
        append_part("P2", bass_spans, include_harmony=False)

    ET.indent(score, space="  ")
    return ET.tostring(score, encoding="unicode", xml_declaration=True)
