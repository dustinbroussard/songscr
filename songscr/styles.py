from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Dict, List, Optional

from .ast import Bar, Cell, Section, Song, Track
from .bass import generate_bass_events_from_chords
from .core import resolve_bass_octave, resolve_bass_pattern, resolve_bass_rhythm

KNOWN_STYLES = {"slowblues", "straightrock", "funklite"}
KNOWN_DRUM_PATTERNS = {"halftimeshuffle", "straight8rock", "fouronfloor", "funk16kick", "balladsparse"}
KNOWN_BASS_PATTERNS = {"root", "root5", "octave", "walkup", "pedal"}
KNOWN_TEMPLATE_MODES = {"fillmissing"}

STYLE_DEFAULTS = {
    "slowblues": {"drum_pattern": "halftimeshuffle", "bass_pattern": "root5"},
    "straightrock": {"drum_pattern": "straight8rock", "bass_pattern": "root"},
    "funklite": {"drum_pattern": "funk16kick", "bass_pattern": "octave"},
}


@dataclass
class StyleContext:
    style: Optional[str]
    drum_pattern: Optional[str]
    bass_pattern: Optional[str]
    bass_octave: int
    bass_rhythm: str
    template_mode: str


@dataclass
class GeneratedSectionTracks:
    drums: Optional[Track] = None
    bass: Optional[Track] = None


def _find_last_tag_value(tags, name: str) -> Optional[str]:
    target = name.strip().lower()
    for tag in reversed(tags):
        if tag.name.strip().lower() == target:
            return tag.value
    return None


def _section_index(song: Song, section: Section) -> int:
    for idx, candidate in enumerate(song.sections):
        if candidate is section:
            return idx
    for idx, candidate in enumerate(song.sections):
        if candidate.name == section.name:
            return idx
    return -1


def _track_has_content(track: Optional[Track]) -> bool:
    if track is None:
        return False
    for bar in track.bars:
        for cell in bar.cells:
            if any(token not in ("", ".", "R", "(R)", "%") for token in cell.tokens):
                return True
    return False


def _midi_to_note_name(midi_note: int) -> str:
    names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    octave = (midi_note // 12) - 1
    return f"{names[midi_note % 12]}{octave}"


def resolve_style_context(song: Song, section: Section, track_name=None) -> StyleContext:
    section_index = _section_index(song, section)
    section_tags = section.tags if section_index >= 0 else []
    track = section.tracks.get(track_name) if track_name else None

    style = _find_last_tag_value(song.tags, "style")
    section_style = _find_last_tag_value(section_tags, "style")
    if section_style:
        style = section_style
    style_norm = style.strip().lower() if style else None

    drum_pattern = _find_last_tag_value(song.tags, "drum pattern")
    section_drum = _find_last_tag_value(section_tags, "drum pattern")
    if section_drum:
        drum_pattern = section_drum
    drum_norm = drum_pattern.strip().lower() if drum_pattern else None

    bass_pattern = resolve_bass_pattern(song, SimpleNamespace(section_index=section_index), track)
    if bass_pattern is None and style_norm in STYLE_DEFAULTS:
        bass_pattern = STYLE_DEFAULTS[style_norm]["bass_pattern"]
    if drum_norm is None and style_norm in STYLE_DEFAULTS:
        drum_norm = STYLE_DEFAULTS[style_norm]["drum_pattern"]

    template_mode = _find_last_tag_value(song.tags, "template mode")
    section_mode = _find_last_tag_value(section_tags, "template mode")
    if section_mode:
        template_mode = section_mode
    template_mode_norm = template_mode.strip().lower() if template_mode else "fillmissing"
    if template_mode_norm not in KNOWN_TEMPLATE_MODES:
        template_mode_norm = "fillmissing"

    return StyleContext(
        style=style_norm if style_norm in KNOWN_STYLES else style_norm,
        drum_pattern=drum_norm if drum_norm in KNOWN_DRUM_PATTERNS else drum_norm,
        bass_pattern=bass_pattern if bass_pattern in KNOWN_BASS_PATTERNS else bass_pattern,
        bass_octave=resolve_bass_octave(song, SimpleNamespace(section_index=section_index), track),
        bass_rhythm=resolve_bass_rhythm(song, SimpleNamespace(section_index=section_index), track),
        template_mode=template_mode_norm,
    )


def _drum_cells_from_slots(slots: List[str]) -> List[Cell]:
    return [Cell(raw=slot, tokens=[] if slot == "" else [slot], tags=[]) for slot in slots]


def _generate_pattern_slots(pattern_name: str, quantize: int) -> List[str]:
    if quantize == 8:
        patterns = {
            "halftimeshuffle": ["K", "H", ".", "H", "S", "H", ".", "H"],
            "straight8rock": ["K", "H", "S", "H", "K", "H", "S", "H"],
            "fouronfloor": ["K", "H", "K", "H", "K", "H", "K", "H"],
            "funk16kick": ["K", "H", ".", "H", "S", "H", "K", "H"],
            "balladsparse": ["K", ".", ".", "H", "S", ".", ".", "H"],
        }
        return patterns.get(pattern_name, ["."] * 8)
    patterns16 = {
        "halftimeshuffle": ["K", ".", "H", ".", ".", ".", "H", ".", "S", ".", "H", ".", ".", ".", "H", "."],
        "straight8rock": ["K", ".", "H", ".", "S", ".", "H", ".", "K", ".", "H", ".", "S", ".", "H", "."],
        "fouronfloor": ["K", ".", "H", ".", "K", ".", "H", ".", "K", ".", "H", ".", "K", ".", "H", "."],
        "funk16kick": ["K", ".", "H", "H", ".", "K", "H", ".", "S", ".", "H", "H", "K", ".", "H", "."],
        "balladsparse": ["K", ".", ".", ".", ".", ".", "H", ".", "S", ".", ".", ".", ".", ".", "H", "."],
    }
    return patterns16.get(pattern_name, ["."] * 16)


def generate_drum_pattern(pattern_name, time_signature, quantize, bars, section_name) -> List[Bar]:
    if time_signature != (4, 4):
        return []
    slots = _generate_pattern_slots(pattern_name, quantize)
    return [Bar(index=bar_index + 1, cells=_drum_cells_from_slots(slots)) for bar_index in range(bars)]


def _materialize_bass_track(section: Section, song: Song, section_index: int, pattern: str, rhythm: str, octave: int, quantize: int, bars: int) -> Optional[Track]:
    chords_track = section.tracks.get("Chords") or section.tracks.get("Chord")
    if chords_track is None:
        return None
    bar_ticks = 480 * 4
    beat_ticks = 480
    events = generate_bass_events_from_chords(
        section,
        pattern,
        rhythm,
        octave,
        {
            "take_number": 1,
            "abs_cursor": 0,
            "bar_duration": bar_ticks,
            "beat_duration": beat_ticks,
            "beats_per_bar": 4,
            "max_bars": bars,
        },
    )
    if not events:
        return None
    slots_per_bar = 8 if quantize == 8 else 16
    slot_ticks = beat_ticks // 2 if quantize == 8 else beat_ticks // 4
    generated_bars: List[Bar] = []
    for bar_index in range(bars):
        cells = [Cell(raw="", tokens=[], tags=[]) for _ in range(slots_per_bar)]
        bar_start = bar_index * bar_ticks
        for event in [ev for ev in events if bar_start <= ev.start < bar_start + bar_ticks]:
            slot_index = int((event.start - bar_start) // slot_ticks)
            if 0 <= slot_index < len(cells):
                dashes = max(0, int(round((event.duration - slot_ticks) / max(1, slot_ticks))))
                token = _midi_to_note_name(event.midi_note) + ("-" * dashes)
                if event.velocity != 90:
                    token += f"({event.velocity})"
                cells[slot_index].tokens = [token]
        generated_bars.append(Bar(index=bar_index + 1, cells=cells))
    return Track(name="Bass", tags=[], bars=generated_bars)


def expand_section_templates(section, resolved_context, timing_context, chords_track) -> GeneratedSectionTracks:
    if resolved_context.template_mode != "fillmissing":
        return GeneratedSectionTracks()

    bars = int(timing_context["bars"])
    quantize = int(timing_context["quantize"])
    time_signature = timing_context["time_signature"]
    generated = GeneratedSectionTracks()

    if not _track_has_content(section.tracks.get("Drums")) and resolved_context.drum_pattern in KNOWN_DRUM_PATTERNS:
        drum_bars = generate_drum_pattern(resolved_context.drum_pattern, time_signature, quantize, bars, section.name)
        if drum_bars:
            generated.drums = Track(name="Drums", tags=[], bars=drum_bars)

    if not _track_has_content(section.tracks.get("Bass")) and resolved_context.bass_pattern in KNOWN_BASS_PATTERNS and chords_track is not None:
        generated.bass = _materialize_bass_track(
            section,
            timing_context["song"],
            timing_context["section_index"],
            resolved_context.bass_pattern,
            resolved_context.bass_rhythm,
            resolved_context.bass_octave,
            quantize,
            bars,
        )

    return generated


def expand_song_templates(song: Song) -> Song:
    expanded = deepcopy(song)
    time_signature = (
        int(str(expanded.meta.get("time signature", "4/4")).split("/")[0]),
        int(str(expanded.meta.get("time signature", "4/4")).split("/")[1]),
    ) if isinstance(expanded.meta.get("time signature"), str) and "/" in str(expanded.meta.get("time signature")) else (4, 4)
    quantize_value = expanded.meta.get("quantize")
    quantize = 8 if str(quantize_value).lower().startswith("8") else 16

    for section_index, section in enumerate(expanded.sections):
        max_bars = 0
        for track in section.tracks.values():
            max_bars = max(max_bars, len(track.bars))
        chords_track = section.tracks.get("Chords") or section.tracks.get("Chord")
        context = resolve_style_context(expanded, section, track_name="Bass")
        generated = expand_section_templates(
            section,
            context,
            {
                "bars": max_bars or (len(chords_track.bars) if chords_track is not None else 0),
                "quantize": quantize,
                "time_signature": time_signature,
                "song": expanded,
                "section_index": section_index,
            },
            chords_track,
        )
        if generated.drums is not None and not _track_has_content(section.tracks.get("Drums")):
            section.tracks["Drums"] = generated.drums
        if generated.bass is not None and not _track_has_content(section.tracks.get("Bass")):
            section.tracks["Bass"] = generated.bass

    return expanded
