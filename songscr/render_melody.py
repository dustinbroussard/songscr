from __future__ import annotations

from typing import Dict, List

from .core import _parse_bracket_group, _parse_melody_event_tokens, _token_is_bracket_group, _melody_grid_slots_per_bar, _melody_grid_mode, _melody_cell_step_ticks
from .midi import MidiEvent, cc, meta_lyric, note_off, note_on, pitch_bend, semitones_to_pitchbend

def _vibrato_depth_to_cc1(depth: int) -> int:
    clamped = max(0, min(9, depth))
    return int(round((clamped * 127) / 9))


def _schedule_melody_expr(
    melody_events: List[MidiEvent],
    last_ticks: Dict[str, int],
    append_abs,
    start_tick: int,
    base_duration_ticks: int,
    token_spec,
    grid_unit_ticks: int,
    bend_range_semitones: int,
    lyric_schedule,
) -> None:
    vel = token_spec.velocity
    if token_spec.ghost:
        vel = max(1, int(round(vel * 0.2)))
    duration_ticks = base_duration_ticks + token_spec.dashes * grid_unit_ticks
    off_tick = start_tick + duration_ticks
    has_pitch_bend = token_spec.bend_semitones is not None or token_spec.ramp_target_note is not None

    if has_pitch_bend:
        append_abs(melody_events, start_tick, last_ticks, "melody", pitch_bend(0, 1, 8192))
    if token_spec.vibrato_depth is not None:
        append_abs(
            melody_events,
            start_tick,
            last_ticks,
            "melody",
            cc(0, 1, 1, _vibrato_depth_to_cc1(token_spec.vibrato_depth)),
        )
    lyric_texts = lyric_schedule.get(start_tick, [])
    if lyric_texts:
        append_abs(melody_events, start_tick, last_ticks, "melody", meta_lyric(0, lyric_texts.pop(0)))
    append_abs(melody_events, start_tick, last_ticks, "melody", note_on(0, 1, token_spec.midi_note, vel))

    if token_spec.bend_semitones is not None:
        bend_tick = start_tick + int(round(duration_ticks * 0.5))
        append_abs(
            melody_events,
            bend_tick,
            last_ticks,
            "melody",
            pitch_bend(0, 1, semitones_to_pitchbend(token_spec.bend_semitones, range_semitones=bend_range_semitones)),
        )
    if token_spec.ramp_target_note is not None:
        delta = token_spec.ramp_target_note - token_spec.midi_note
        append_abs(
            melody_events,
            off_tick,
            last_ticks,
            "melody",
            pitch_bend(0, 1, semitones_to_pitchbend(delta, range_semitones=bend_range_semitones)),
        )

    append_abs(melody_events, off_tick, last_ticks, "melody", note_off(0, 1, token_spec.midi_note, 0))
    if token_spec.vibrato_depth is not None:
        append_abs(melody_events, off_tick, last_ticks, "melody", cc(0, 1, 1, 0))
    if has_pitch_bend:
        append_abs(melody_events, off_tick, last_ticks, "melody", pitch_bend(0, 1, 8192))


def render_melody_bar(
    melody_events: List[MidiEvent],
    last_ticks: Dict[str, int],
    append_abs,
    abs_cursor: int,
    bar_cells,
    beats_per_bar: float,
    quantize: int,
    ticks_per_beat: int,
    bar_ticks: int,
    grid_unit_ticks: int,
    bend_range_semitones: int,
    lyric_schedule,
) -> None:
    cell_count = len(bar_cells)
    beats_per_bar_int = int(round(beats_per_bar))
    grid_slots_per_bar = _melody_grid_slots_per_bar(beats_per_bar, quantize)
    mode = _melody_grid_mode(cell_count, beats_per_bar_int, grid_slots_per_bar)
    cell_step_ticks = _melody_cell_step_ticks(mode, ticks_per_beat, grid_unit_ticks, bar_ticks, cell_count)

    for ci, cell in enumerate(bar_cells):
        tokens = cell.tokens
        if not tokens:
            continue
        cell_start = abs_cursor + int(round(ci * cell_step_ticks))

        if mode == "beat":
            bracket_tokens = [tok for tok in tokens if _token_is_bracket_group(tok)]
            if bracket_tokens:
                for tok in bracket_tokens:
                    inner_tokens = _parse_bracket_group(tok)
                    if not inner_tokens:
                        continue
                    inner_ticks = ticks_per_beat / len(inner_tokens)
                    idx = 0
                    while idx < len(inner_tokens):
                        token_spec, consumed = _parse_melody_event_tokens(inner_tokens, idx, 90)
                        consumed = max(1, consumed)
                        if token_spec is not None:
                            inner_start = cell_start + int(round(idx * inner_ticks))
                            base_dur = max(1, int(round(inner_ticks)))
                            _schedule_melody_expr(
                                melody_events,
                                last_ticks,
                                append_abs,
                                inner_start,
                                base_dur,
                                token_spec,
                                grid_unit_ticks,
                                bend_range_semitones,
                                lyric_schedule,
                            )
                        idx += consumed
                continue

        sub_count = max(1, len(tokens))
        sub_ticks = cell_step_ticks / sub_count
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            slot_start = cell_start + int(round(idx * sub_ticks))
            slot_ticks = max(1, int(round(sub_ticks)))
            if _token_is_bracket_group(tok):
                inner_tokens = _parse_bracket_group(tok)
                if inner_tokens:
                    inner_ticks = sub_ticks / len(inner_tokens)
                    inner_idx = 0
                    while inner_idx < len(inner_tokens):
                        token_spec, consumed = _parse_melody_event_tokens(inner_tokens, inner_idx, 90)
                        consumed = max(1, consumed)
                        if token_spec is not None:
                            inner_start = slot_start + int(round(inner_idx * inner_ticks))
                            base_dur = max(1, int(round(inner_ticks)))
                            _schedule_melody_expr(
                                melody_events,
                                last_ticks,
                                append_abs,
                                inner_start,
                                base_dur,
                                token_spec,
                                grid_unit_ticks,
                                bend_range_semitones,
                                lyric_schedule,
                            )
                        inner_idx += consumed
                idx += 1
                continue
            token_spec, consumed = _parse_melody_event_tokens(tokens, idx, 90)
            consumed = max(1, consumed)
            if token_spec is not None:
                _schedule_melody_expr(
                    melody_events,
                    last_ticks,
                    append_abs,
                    slot_start,
                    slot_ticks,
                    token_spec,
                    grid_unit_ticks,
                    bend_range_semitones,
                    lyric_schedule,
                )
            idx += consumed
