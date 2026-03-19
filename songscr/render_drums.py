from __future__ import annotations

from typing import Dict, List

from .core import _parse_bracket_group, _token_is_bracket_group
from .midi import MidiEvent, note_off, note_on


def _drum_grid_params(beats_per_bar: float, quantize: int) -> tuple[int, int]:
    slots_per_beat = 4 if quantize == 16 else 2
    raw = beats_per_bar * slots_per_beat
    if abs(raw - round(raw)) > 1e-9:
        # MVP fallback: non-integer grid with requested quantize falls back to 16th.
        slots_per_beat = 4
        raw = beats_per_bar * slots_per_beat
    return slots_per_beat, int(round(raw))


def _normalize_drum_bracket_tokens(inner_tokens: List[str], slots_per_beat: int) -> List[str]:
    if len(inner_tokens) >= slots_per_beat:
        return inner_tokens[:slots_per_beat]
    return inner_tokens + ["."] * (slots_per_beat - len(inner_tokens))


def render_drums_bar(
    drum_events: List[MidiEvent],
    last_ticks: Dict[str, int],
    abs_cursor: int,
    bar_cells,
    beats_per_bar: float,
    quantize: int,
    bar_ticks: int,
    drum_map: Dict[str, int],
    append_abs,
) -> None:
    cells = bar_cells
    cell_count = len(cells)
    beats_per_bar_int = int(round(beats_per_bar))
    slots_per_beat, grid_slots_per_bar = _drum_grid_params(beats_per_bar, quantize)
    drum_vel = 100
    slot_ticks = bar_ticks / max(1, grid_slots_per_bar)

    def schedule_slot(slot_index: int, tok: str) -> None:
        if tok == ".":
            return
        note = drum_map.get(tok)
        if note is None:
            return
        start = abs_cursor + int(round(slot_index * slot_ticks))
        dur = max(1, int(round(slot_ticks * 0.25)))
        append_abs(drum_events, start, last_ticks, "drums", note_on(0, 9, note, drum_vel))
        append_abs(drum_events, start + dur, last_ticks, "drums", note_off(0, 9, note, 0))

    if cell_count == beats_per_bar_int:
        # Beat-grid: each cell is a beat, optionally expanded by one bracket group.
        for beat_idx, cell in enumerate(cells):
            beat_slot_start = beat_idx * slots_per_beat
            slot_tokens = ["."] * slots_per_beat
            if cell.tokens:
                first = cell.tokens[0]
                if _token_is_bracket_group(first):
                    inner_tokens = _parse_bracket_group(first)
                    slot_tokens = _normalize_drum_bracket_tokens(inner_tokens, slots_per_beat)
                else:
                    slot_tokens[0] = first
            for rel_slot, tok in enumerate(slot_tokens):
                schedule_slot(beat_slot_start + rel_slot, tok)
        return

    if cell_count == grid_slots_per_bar:
        # Quantize-grid: each cell is one slot, brackets are ignored.
        for slot_idx, cell in enumerate(cells):
            if not cell.tokens:
                continue
            tok = cell.tokens[0]
            if _token_is_bracket_group(tok):
                continue
            schedule_slot(slot_idx, tok)
        return

    # Fallback for unexpected drum grids: flatten non-bracket tokens across the bar.
    tokens = [tok for cell in cells for tok in cell.tokens if not _token_is_bracket_group(tok)]
    if not tokens:
        return
    step_ticks = bar_ticks / len(tokens)
    for i, tok in enumerate(tokens):
        if tok == ".":
            continue
        note = drum_map.get(tok)
        if note is None:
            continue
        start = abs_cursor + int(round(i * step_ticks))
        dur = max(1, int(round(step_ticks * 0.25)))
        append_abs(drum_events, start, last_ticks, "drums", note_on(0, 9, note, drum_vel))
        append_abs(drum_events, start + dur, last_ticks, "drums", note_off(0, 9, note, 0))
