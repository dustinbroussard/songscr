from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import re

from .chords import parse_chord_symbol, pitch_class_to_midi
_ALT_ENDING_RE = re.compile(r"^\{(\d+)\}$")


@dataclass
class BassEvent:
    start: int
    duration: int
    midi_note: int
    velocity: int = 78
    generated: bool = True




def _normalize_bass_range(midi_note: int) -> int:
    while midi_note < 28:
        midi_note += 12
    while midi_note > 60:
        midi_note -= 12
    return max(0, min(127, midi_note))


def _spec_for_token(token: str):
    cleaned = re.sub(r"\([^)]+\)$", "", token).strip()
    return parse_chord_symbol(cleaned)


def chord_token_to_root_midi(token: str, octave: int) -> int:
    spec = _spec_for_token(token)
    if spec is None:
        raise ValueError("Chord token has no recognizable root.")
    pitch_class = spec.slash_bass or spec.root
    midi_note = pitch_class_to_midi(pitch_class, octave)
    if midi_note is None:
        raise ValueError("Chord token root is out of range.")
    return _normalize_bass_range(midi_note)


def chord_token_to_fifth_midi(token: str, octave: int) -> int:
    root = chord_token_to_root_midi(token, octave)
    return _normalize_bass_range(root + 7)


def _chord_token_to_third_midi(token: str, octave: int) -> int:
    spec = _spec_for_token(token)
    if spec is None:
        return chord_token_to_root_midi(token, octave)
    root = chord_token_to_root_midi(token, octave)
    interval = 3 if spec.quality in ("min", "dim") else 4
    return _normalize_bass_range(root + interval)


def _chord_token_to_octave_midi(token: str, octave: int) -> int:
    return _normalize_bass_range(chord_token_to_root_midi(token, octave) + 12)


def _approach_note(current_note: int, next_root: int) -> int:
    if next_root >= current_note:
        return _normalize_bass_range(next_root - 1)
    return _normalize_bass_range(next_root + 1)


def generate_bass_events_from_chords(section, pattern, rhythm, octave, timing_context) -> List[BassEvent]:
    from .core import _tokens_for_take
    chords_track = section.tracks.get("Chords") or section.tracks.get("Chord")
    if chords_track is None:
        return []

    take_number = int(timing_context["take_number"])
    abs_cursor = int(timing_context["abs_cursor"])
    bar_duration = int(timing_context["bar_duration"])
    beat_duration = int(timing_context["beat_duration"])
    beats_per_bar = int(timing_context["beats_per_bar"])

    beat_cells: List[Dict[str, object]] = []
    for bar_index, bar in enumerate(chords_track.bars):
        if bar_index >= int(timing_context.get("max_bars", len(chords_track.bars))):
            break
        filtered_cells = [_tokens_for_take(cell.tokens, take_number) for cell in bar.cells]
        for beat_index, tokens in enumerate(filtered_cells[:beats_per_bar]):
            token = ""
            if tokens:
                token = re.sub(r"\([^)]+\)$", "", tokens[0]).strip()
                token = re.sub(r"\{[0-9\-]+\}$", "", token).strip()
            if token in ("", "R", "(R)", "%"):
                beat_cells.append({"token": None, "start": abs_cursor + (bar_index * bar_duration) + (beat_index * beat_duration)})
            else:
                beat_cells.append({"token": token, "start": abs_cursor + (bar_index * bar_duration) + (beat_index * beat_duration)})

    events: List[BassEvent] = []
    active_tokens = [cell["token"] for cell in beat_cells]
    for index, cell in enumerate(beat_cells):
        token = cell["token"]
        if token is None:
            continue
        start = int(cell["start"])
        next_token = None
        for later in active_tokens[index + 1 :]:
            if later is not None:
                next_token = later
                break

        if pattern in ("root", "pedal"):
            tone = chord_token_to_root_midi(token, octave)
        elif pattern == "root5":
            tone = chord_token_to_root_midi(token, octave) if index % 2 == 0 else chord_token_to_fifth_midi(token, octave)
        elif pattern == "octave":
            tone = chord_token_to_root_midi(token, octave) if index % 2 == 0 else _chord_token_to_octave_midi(token, octave)
        elif pattern == "walkup":
            run_length = 1
            for later in active_tokens[index + 1 :]:
                if later == token:
                    run_length += 1
                else:
                    break
            run_pos = 0
            for prev in reversed(active_tokens[:index]):
                if prev == token:
                    run_pos += 1
                else:
                    break
            if next_token is None or run_length <= 1:
                tone = chord_token_to_root_midi(token, octave)
            elif run_pos == 0:
                tone = chord_token_to_root_midi(token, octave)
            elif run_pos == run_length - 1:
                tone = _approach_note(chord_token_to_root_midi(token, octave), chord_token_to_root_midi(next_token, octave))
            elif run_pos == 1:
                tone = _chord_token_to_third_midi(token, octave)
            else:
                tone = chord_token_to_fifth_midi(token, octave)
        else:
            tone = chord_token_to_root_midi(token, octave)

        if rhythm == "eighths":
            half = max(1, beat_duration // 2)
            events.append(BassEvent(start=start, duration=half, midi_note=tone))
            events.append(BassEvent(start=start + half, duration=beat_duration - half if beat_duration - half > 0 else half, midi_note=tone))
        else:
            events.append(BassEvent(start=start, duration=beat_duration, midi_note=tone))

    return events
