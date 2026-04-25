from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import re

from .ast import Bar, Song, Tag, Track
from .guitar_voicings import STANDARD_GUITAR_TUNING, parse_guitar_tuning

NOTE_RE = re.compile(r"^[A-G](?:#|b)?[0-9]$")
CHORD_RE = re.compile(r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus)?[0-9]*(?:add[0-9]+)?(?:[#b][0-9]+)?(?:alt)?(?:/[A-G](?:#|b)?)?$")


@dataclass
class MelodyEventSpec:
    source_token: str
    midi_note: int
    velocity: int
    dashes: int
    ghost: bool
    bend_semitones: Optional[int] = None
    vibrato_depth: Optional[int] = None
    ramp_target_note: Optional[int] = None
    invalid_bend: bool = False
    invalid_vibrato: bool = False


@dataclass
class TimedMelodyEvent:
    start: int
    duration: int
    midi_note: int
    source_token: str
    velocity: int = 90
    section: Optional[str] = None
    section_instance: Optional[int] = None
    bar: Optional[int] = None


@dataclass
class TimedBassEvent:
    start: int
    duration: int
    midi_note: int
    velocity: int
    source_token: str
    section: Optional[str] = None
    section_instance: Optional[int] = None
    bar: Optional[int] = None


def note_to_midi(note: str) -> Optional[int]:
    match = NOTE_RE.match(note)
    if not match:
        return None
    pitch_class = note[0]
    rest = note[1:]
    accidental = ""
    if rest and rest[0] in "#b":
        accidental = rest[0]
        octave = int(rest[1:])
    else:
        octave = int(rest)
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[pitch_class]
    if accidental == "#":
        base += 1
    elif accidental == "b":
        base -= 1
    midi = 12 * (octave + 1) + base
    return midi if 0 <= midi <= 127 else None


def parse_quantize(song: Song) -> int:
    quantize = song.meta.get("quantize")
    if quantize is None:
        return 16
    match = re.search(r"(\d+)", str(quantize).strip().lower())
    if not match:
        return 16
    return 8 if int(match.group(1)) == 8 else 16


def parse_time_signature(song: Song) -> Tuple[int, int]:
    time_signature = song.meta.get("time signature")
    if isinstance(time_signature, str) and "/" in time_signature:
        left, right = time_signature.split("/", 1)
        try:
            return int(left.strip()), int(right.strip())
        except ValueError:
            pass
    return 4, 4


def parse_tempo(song: Song) -> int:
    tempo = song.meta.get("tempo")
    if tempo is None:
        return 120
    try:
        return int(str(tempo).strip())
    except ValueError:
        return 120


def grid_unit_ticks(ticks_per_beat: int, quantize: int) -> int:
    return int(round(ticks_per_beat * (4 / quantize)))


def dyn_to_velocity(text: str) -> Optional[int]:
    return {"pp": 30, "p": 45, "mp": 60, "mf": 75, "f": 95, "ff": 115}.get(text.strip().lower())


def extract_velocity(token: str) -> Optional[int]:
    match = re.search(r"\(([^)]+)\)$", token)
    if not match:
        return None
    value = match.group(1).strip()
    if value.isdigit():
        return max(0, min(127, int(value)))
    return dyn_to_velocity(value)


def strip_paren_dyn(token: str) -> str:
    return re.sub(r"\([^)]+\)$", "", token)


def strip_dyn_if_present(token: str) -> str:
    return strip_paren_dyn(token) if extract_velocity(token) is not None else token


def melody_token_is_note_or_rest(token: str) -> bool:
    if token in ("R", "(R)"):
        return True
    text = token
    if text.startswith("~"):
        text = text[1:]
        if text == "":
            return True
    if extract_velocity(text) is not None:
        text = re.sub(r"\([^\)]*\)$", "", text)
    text = re.sub(r"[-^]+$", "", text)
    text = re.sub(r"(v\d+|b\d+|v|b)$", "", text)
    if "/" in text and NOTE_RE.match(text.split("/")[0]) and NOTE_RE.match(text.split("/")[1]):
        return True
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return note_to_midi(text) is not None


def parse_pitch_bend_range_value(raw_value: Optional[str]) -> Optional[int]:
    if raw_value is None:
        return None
    try:
        return int(str(raw_value).strip())
    except ValueError:
        return None


def find_last_tag_value(tags: List[Tag], tag_name: str) -> Optional[str]:
    target = tag_name.strip().lower()
    for tag in reversed(tags):
        if tag.name.strip().lower() == target:
            return tag.value
    return None


def bar_tags(bar: Optional[Bar]) -> List[Tag]:
    if bar is None:
        return []
    return [tag for cell in bar.cells for tag in cell.tags]


def resolve_scoped_tag_value(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar], tag_name: str) -> Optional[str]:
    resolved = find_last_tag_value(song.tags, tag_name)
    if isinstance(section_index, int) and 0 <= section_index < len(song.sections):
        section_value = find_last_tag_value(song.sections[section_index].tags, tag_name)
        if section_value is not None:
            resolved = section_value
    if track is not None:
        track_value = find_last_tag_value(track.tags, tag_name)
        if track_value is not None:
            resolved = track_value
    bar_value = find_last_tag_value(bar_tags(bar), tag_name)
    if bar_value is not None:
        resolved = bar_value
    return resolved


def resolve_chord_voicing(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar]) -> str:
    value = resolve_scoped_tag_value(song, section_index, track, bar, "voicing")
    if value is None:
        return "piano"
    lowered = str(value).strip().lower()
    return lowered if lowered in ("guitar", "piano") else "piano"


def resolve_voice_leading(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar]) -> Optional[str]:
    value = resolve_scoped_tag_value(song, section_index, track, bar, "voice leading")
    if value is None:
        return None
    lowered = str(value).strip().lower()
    return lowered or None


def resolve_capo(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar]) -> int:
    value = resolve_scoped_tag_value(song, section_index, track, bar, "capo")
    try:
        return max(0, int(str(value).strip())) if value is not None else 0
    except ValueError:
        return 0


def resolve_guitar_tuning(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar]) -> List[int]:
    value = resolve_scoped_tag_value(song, section_index, track, bar, "guitar tuning")
    if value is None:
        return list(STANDARD_GUITAR_TUNING)
    try:
        return parse_guitar_tuning(value)
    except ValueError:
        return list(STANDARD_GUITAR_TUNING)


def resolve_guitar_position(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar]) -> str:
    value = resolve_scoped_tag_value(song, section_index, track, bar, "guitar position")
    if value is None:
        return "Mid"
    lowered = str(value).strip().lower()
    if lowered == "open":
        return "Open"
    if lowered == "high":
        return "High"
    return "Mid"


def resolve_chord_range(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar]) -> Tuple[Optional[int], Optional[int]]:
    value = resolve_scoped_tag_value(song, section_index, track, bar, "chord range")
    if value is None:
        return None, None
    match = re.match(r"^\s*([A-G](?:#|b)?-?\d+)\s*\.\.\s*([A-G](?:#|b)?-?\d+)\s*$", str(value))
    if match is None:
        return None, None
    low = note_to_midi(match.group(1))
    high = note_to_midi(match.group(2))
    if low is None or high is None:
        return None, None
    return (high, low) if low > high else (low, high)


def has_explicit_pitch_bend_range(song: Song) -> bool:
    for tag in song.tags:
        if tag.name.strip().lower() == "pitch bend range":
            return True
    for section in song.sections:
        for tag in section.tags:
            if tag.name.strip().lower() == "pitch bend range":
                return True
        for track in section.tracks.values():
            for tag in track.tags:
                if tag.name.strip().lower() == "pitch bend range":
                    return True
    return False


def resolve_pitch_bend_range(song: Song, section_instance, track: Optional[Track]) -> int:
    resolved = 2
    parsed_global = parse_pitch_bend_range_value(find_last_tag_value(song.tags, "pitch bend range"))
    if parsed_global is not None and 1 <= parsed_global <= 24:
        resolved = parsed_global

    section_index = getattr(section_instance, "section_index", None)
    if isinstance(section_index, int) and 0 <= section_index < len(song.sections):
        parsed_section = parse_pitch_bend_range_value(find_last_tag_value(song.sections[section_index].tags, "pitch bend range"))
        if parsed_section is not None and 1 <= parsed_section <= 24:
            resolved = parsed_section

    if track is not None:
        parsed_track = parse_pitch_bend_range_value(find_last_tag_value(track.tags, "pitch bend range"))
        if parsed_track is not None and 1 <= parsed_track <= 24:
            resolved = parsed_track
    return resolved


def parse_melody_note_expression(token: str, default_vel: int) -> Optional[MelodyEventSpec]:
    if token in ("R", "(R)", "%", ">>"):
        return None
    text = token
    velocity = extract_velocity(text) or default_vel
    text = strip_dyn_if_present(text)
    if text.startswith("~"):
        text = text[1:]
        if text == "":
            return None
    sustain_match = re.search(r"(-+)$", text)
    dashes = len(sustain_match.group(1)) if sustain_match else 0
    text = re.sub(r"(-+)$", "", text)
    text = text.rstrip("^")
    ghost = False
    if text.startswith("(") and text.endswith(")"):
        ghost = True
        text = text[1:-1]
    if "/" in text:
        text = text.split("/", 1)[0]

    match = re.match(r"^(?P<note>[A-G](?:#|b)?[0-9])(?:(?P<kind>[bv])(?P<amount>.*))?$", text)
    if match is None:
        return None

    midi_note = note_to_midi(match.group("note"))
    if midi_note is None:
        return None

    kind = match.group("kind")
    amount_text = match.group("amount")
    bend_semitones: Optional[int] = None
    vibrato_depth: Optional[int] = None
    invalid_bend = False
    invalid_vibrato = False
    if kind == "b":
        if amount_text == "":
            bend_semitones = 1
        elif amount_text is not None and amount_text.isdigit():
            bend_semitones = int(amount_text)
        else:
            invalid_bend = True
    if kind == "v":
        if amount_text == "":
            vibrato_depth = 3
        elif amount_text is not None and amount_text.isdigit():
            vibrato_depth = int(amount_text)
        else:
            invalid_vibrato = True

    return MelodyEventSpec(
        source_token=token,
        midi_note=midi_note,
        velocity=velocity,
        dashes=dashes,
        ghost=ghost,
        bend_semitones=bend_semitones,
        vibrato_depth=vibrato_depth,
        invalid_bend=invalid_bend,
        invalid_vibrato=invalid_vibrato,
    )


def parse_melody_note_token(token: str, default_vel: int) -> Optional[Tuple[int, int, int, bool]]:
    parsed = parse_melody_note_expression(token, default_vel)
    if parsed is None:
        return None
    return parsed.midi_note, parsed.velocity, parsed.dashes, parsed.ghost


def parse_melody_event_tokens(tokens: List[str], index: int, default_vel: int) -> Tuple[Optional[MelodyEventSpec], int]:
    if index >= len(tokens):
        return None, 1
    token = tokens[index]
    if ">>" in token and token != ">>":
        left, right = token.split(">>", 1)
        start = parse_melody_note_expression(left.strip(), default_vel)
        end = parse_melody_note_expression(right.strip(), default_vel)
        if start is None or end is None:
            return None, 1
        end_dashes = end.dashes if end.dashes > 0 else start.dashes
        return MelodyEventSpec(
            source_token=token,
            midi_note=start.midi_note,
            velocity=end.velocity if right.strip() else start.velocity,
            dashes=end_dashes,
            ghost=start.ghost,
            ramp_target_note=end.midi_note,
            invalid_bend=start.invalid_bend or end.invalid_bend,
            invalid_vibrato=start.invalid_vibrato or end.invalid_vibrato,
        ), 1
    if index + 2 < len(tokens) and tokens[index + 1] == ">>":
        start = parse_melody_note_expression(tokens[index], default_vel)
        end = parse_melody_note_expression(tokens[index + 2], default_vel)
        if start is None or end is None:
            return None, 1
        end_dashes = end.dashes if end.dashes > 0 else start.dashes
        return MelodyEventSpec(
            source_token=f"{tokens[index]} >> {tokens[index + 2]}",
            midi_note=start.midi_note,
            velocity=end.velocity,
            dashes=end_dashes,
            ghost=start.ghost,
            ramp_target_note=end.midi_note,
            invalid_bend=start.invalid_bend or end.invalid_bend,
            invalid_vibrato=start.invalid_vibrato or end.invalid_vibrato,
        ), 3
    return parse_melody_note_expression(token, default_vel), 1


def token_is_bracket_group(token: str) -> bool:
    return token.startswith("[") and token.endswith("]")


def parse_bracket_group(token: str) -> List[str]:
    inner = token[1:-1].strip()
    return inner.split() if inner else []


def drum_token_valid(token: str) -> bool:
    return token in ("K", "S", "H", "O", "C", "T", ".")


def melody_grid_slots_per_bar(beats_per_bar: float, quantize: int) -> int:
    return int(round(beats_per_bar * (quantize / 4)))


def melody_grid_mode(cell_count: int, beats_per_bar_int: int, grid_slots_per_bar: int) -> str:
    if cell_count == beats_per_bar_int:
        return "beat"
    if cell_count == grid_slots_per_bar and cell_count > 0:
        return "quantize"
    return "other"


def melody_cell_step_ticks(mode: str, ticks_per_beat: int, grid_unit_tick_count: int, bar_ticks: int, cell_count: int) -> float:
    if mode == "beat":
        return float(ticks_per_beat)
    if mode == "quantize":
        return float(grid_unit_tick_count)
    if cell_count <= 0:
        return float(bar_ticks)
    return bar_ticks / cell_count


def collect_timed_melody_events_for_bar(
    bar_cells,
    beats_per_bar: float,
    quantize: int,
    ticks_per_beat: int,
    bar_ticks: int,
    grid_unit_tick_count: int,
    bar_start_tick: int,
    *,
    section_name: Optional[str] = None,
    section_instance: Optional[int] = None,
    bar_index: Optional[int] = None,
) -> List[TimedMelodyEvent]:
    events: List[TimedMelodyEvent] = []
    cell_count = len(bar_cells)
    beats_per_bar_int = int(round(beats_per_bar))
    grid_slots_per_bar = melody_grid_slots_per_bar(beats_per_bar, quantize)
    mode = melody_grid_mode(cell_count, beats_per_bar_int, grid_slots_per_bar)
    cell_step = melody_cell_step_ticks(mode, ticks_per_beat, grid_unit_tick_count, bar_ticks, cell_count)

    def add_inner(inner_tokens: List[str], inner_start_base: int, inner_ticks: float) -> None:
        idx = 0
        while idx < len(inner_tokens):
            spec, consumed = parse_melody_event_tokens(inner_tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                start_tick = inner_start_base + int(round(idx * inner_ticks))
                slot_ticks = max(1, int(round(inner_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_tick_count)
                events.append(
                    TimedMelodyEvent(
                        start=start_tick,
                        duration=duration_ticks,
                        midi_note=spec.midi_note,
                        source_token=spec.source_token,
                        velocity=spec.velocity,
                        section=section_name,
                        section_instance=section_instance,
                        bar=bar_index,
                    )
                )
            idx += consumed

    for cell_index, cell in enumerate(bar_cells):
        tokens = cell.tokens
        if not tokens:
            continue
        cell_start = bar_start_tick + int(round(cell_index * cell_step))

        if mode == "beat":
            bracket_tokens = [token for token in tokens if token_is_bracket_group(token)]
            if bracket_tokens:
                for token in bracket_tokens:
                    inner_tokens = parse_bracket_group(token)
                    if inner_tokens:
                        add_inner(inner_tokens, cell_start, ticks_per_beat / len(inner_tokens))
                continue

        sub_count = max(1, len(tokens))
        sub_ticks = cell_step / sub_count
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            slot_start = bar_start_tick + int(round((cell_index * cell_step) + (idx * sub_ticks)))
            if token_is_bracket_group(token):
                inner_tokens = parse_bracket_group(token)
                if inner_tokens:
                    add_inner(inner_tokens, slot_start, sub_ticks / len(inner_tokens))
                idx += 1
                continue
            spec, consumed = parse_melody_event_tokens(tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                slot_ticks = max(1, int(round(sub_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_tick_count)
                events.append(
                    TimedMelodyEvent(
                        start=slot_start,
                        duration=duration_ticks,
                        midi_note=spec.midi_note,
                        source_token=spec.source_token,
                        velocity=spec.velocity,
                        section=section_name,
                        section_instance=section_instance,
                        bar=bar_index,
                    )
                )
            idx += consumed
    return events


def collect_timed_bass_events_for_bar(
    bar_cells,
    beats_per_bar: float,
    quantize: int,
    ticks_per_beat: int,
    bar_ticks: int,
    grid_unit_tick_count: int,
    bar_start_tick: int,
    *,
    section_name: Optional[str] = None,
    section_instance: Optional[int] = None,
    bar_index: Optional[int] = None,
) -> Tuple[List[TimedBassEvent], List[str]]:
    events: List[TimedBassEvent] = []
    ignored_expr_tokens: List[str] = []
    cell_count = len(bar_cells)
    beats_per_bar_int = int(round(beats_per_bar))
    grid_slots_per_bar = melody_grid_slots_per_bar(beats_per_bar, quantize)
    mode = melody_grid_mode(cell_count, beats_per_bar_int, grid_slots_per_bar)
    cell_step = melody_cell_step_ticks(mode, ticks_per_beat, grid_unit_tick_count, bar_ticks, cell_count)

    def add_inner(inner_tokens: List[str], inner_start_base: int, inner_ticks: float) -> None:
        idx = 0
        while idx < len(inner_tokens):
            spec, consumed = parse_melody_event_tokens(inner_tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                if spec.bend_semitones is not None or spec.vibrato_depth is not None or spec.ramp_target_note is not None:
                    ignored_expr_tokens.append(spec.source_token)
                start_tick = inner_start_base + int(round(idx * inner_ticks))
                slot_ticks = max(1, int(round(inner_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_tick_count)
                events.append(
                    TimedBassEvent(
                        start=start_tick,
                        duration=duration_ticks,
                        midi_note=spec.midi_note,
                        velocity=spec.velocity,
                        source_token=spec.source_token,
                        section=section_name,
                        section_instance=section_instance,
                        bar=bar_index,
                    )
                )
            idx += consumed

    for cell_index, cell in enumerate(bar_cells):
        tokens = cell.tokens
        if not tokens:
            continue
        cell_start = bar_start_tick + int(round(cell_index * cell_step))

        if mode == "beat":
            bracket_tokens = [token for token in tokens if token_is_bracket_group(token)]
            if bracket_tokens:
                for token in bracket_tokens:
                    inner_tokens = parse_bracket_group(token)
                    if inner_tokens:
                        add_inner(inner_tokens, cell_start, ticks_per_beat / len(inner_tokens))
                continue

        sub_count = max(1, len(tokens))
        sub_ticks = cell_step / sub_count
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            slot_start = bar_start_tick + int(round((cell_index * cell_step) + (idx * sub_ticks)))
            if token_is_bracket_group(token):
                inner_tokens = parse_bracket_group(token)
                if inner_tokens:
                    add_inner(inner_tokens, slot_start, sub_ticks / len(inner_tokens))
                idx += 1
                continue
            spec, consumed = parse_melody_event_tokens(tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                if spec.bend_semitones is not None or spec.vibrato_depth is not None or spec.ramp_target_note is not None:
                    ignored_expr_tokens.append(spec.source_token)
                slot_ticks = max(1, int(round(sub_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_tick_count)
                events.append(
                    TimedBassEvent(
                        start=slot_start,
                        duration=duration_ticks,
                        midi_note=spec.midi_note,
                        velocity=spec.velocity,
                        source_token=spec.source_token,
                        section=section_name,
                        section_instance=section_instance,
                        bar=bar_index,
                    )
                )
            idx += consumed
    return events, ignored_expr_tokens
