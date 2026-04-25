from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import re

from .ast import Song
from .struct import build_playback_plan
from .timing import (
    TimedMelodyEvent,
    collect_timed_melody_events_for_bar,
    grid_unit_ticks,
    melody_cell_step_ticks,
    melody_grid_mode,
    melody_grid_slots_per_bar,
    parse_bracket_group,
    parse_melody_event_tokens,
    parse_quantize,
    parse_time_signature,
    token_is_bracket_group,
)


@dataclass
class AlignedLyricEvent:
    note_index: int
    note_start: int
    text: Optional[str] = None
    extend: bool = False
    phrase_boundary: bool = False


@dataclass
class LyricsBarAlignment:
    section_name: str
    section_instance: int
    bar_index: int
    melody_event_count: int
    lyric_token_count: int
    overflow_count: int
    orphan_extenders: int
    estimated_syllables: int
    attachments: List[AlignedLyricEvent]


@dataclass
class LyricsSectionReport:
    section_name: str
    section_instance: int
    melody_event_count: int
    lyric_token_count: int
    overflow_count: int
    orphan_extenders: int
    estimated_syllables: int
    bars: List[LyricsBarAlignment]


def lyrics_token_estimated_syllables(token: str) -> int:
    text = token.strip().lower()
    if text in ("", "*", "_"):
        return 0
    text = text.rstrip("/")
    text = re.sub(r"[^a-z]", "", text)
    if not text:
        return 1
    groups = re.findall(r"[aeiouy]+", text)
    count = len(groups)
    if text.endswith("e") and not text.endswith(("le", "ye")) and count > 1:
        count -= 1
    return max(1, count)


def lyric_token_payload(token: str) -> Tuple[str, bool]:
    phrase_boundary = token.endswith("/")
    text = token[:-1] if phrase_boundary else token
    return text, phrase_boundary


def collect_lyric_tokens_for_bar(bar_cells) -> List[str]:
    return [token for cell in bar_cells for token in cell.tokens]


def build_lyrics_alignment_report(song: Song) -> List[LyricsSectionReport]:
    num, den = parse_time_signature(song)
    quantize = parse_quantize(song)
    ticks_per_beat = 480
    beats_per_bar = num * (4 / den)
    bar_tick_count = int(round(beats_per_bar * ticks_per_beat))
    grid_tick_count = grid_unit_ticks(ticks_per_beat, quantize)
    playback_plan, _ = build_playback_plan(song)

    reports: List[LyricsSectionReport] = []
    abs_cursor = 0
    for section_instance in playback_plan:
        section = song.sections[section_instance.section_index]
        max_bars = max((len(track.bars) for track in section.tracks.values()), default=0)
        melody_track = section.tracks.get("Melody")
        lyrics_track = section.tracks.get("Lyrics")
        bar_reports: List[LyricsBarAlignment] = []
        section_melody_count = 0
        section_lyric_tokens = 0
        section_overflow = 0
        section_orphans = 0
        section_syllables = 0
        last_emitted_lyric: Optional[str] = None

        for bar_offset in range(max_bars):
            bar_index = bar_offset + 1
            bar_start_tick = abs_cursor + (bar_offset * bar_tick_count)
            melody_cells = melody_track.bars[bar_offset].cells if melody_track is not None and bar_offset < len(melody_track.bars) else []
            lyrics_cells = lyrics_track.bars[bar_offset].cells if lyrics_track is not None and bar_offset < len(lyrics_track.bars) else []

            melody_events = collect_timed_melody_events_for_bar(
                melody_cells,
                beats_per_bar,
                quantize,
                ticks_per_beat,
                bar_tick_count,
                grid_tick_count,
                bar_start_tick,
                section_name=section.name,
                section_instance=section_instance.instance_number,
                bar_index=bar_index,
            )
            lyric_tokens = collect_lyric_tokens_for_bar(lyrics_cells)
            estimated_syllables = sum(lyrics_token_estimated_syllables(token) for token in lyric_tokens)

            attachments: List[AlignedLyricEvent] = []
            melody_pointer = 0
            orphan_extenders = 0
            overflow_count = 0
            for token in lyric_tokens:
                if token == "*":
                    if melody_pointer < len(melody_events):
                        melody_pointer += 1
                    else:
                        overflow_count += 1
                    continue
                if token == "_":
                    if last_emitted_lyric is None:
                        orphan_extenders += 1
                        if melody_pointer < len(melody_events):
                            melody_pointer += 1
                        continue
                    if melody_pointer < len(melody_events):
                        attachments.append(
                            AlignedLyricEvent(
                                note_index=melody_pointer,
                                note_start=melody_events[melody_pointer].start,
                                extend=True,
                            )
                        )
                        melody_pointer += 1
                    else:
                        overflow_count += 1
                    continue
                if melody_pointer >= len(melody_events):
                    overflow_count += 1
                    continue
                lyric_text, phrase_boundary = lyric_token_payload(token)
                attachments.append(
                    AlignedLyricEvent(
                        note_index=melody_pointer,
                        note_start=melody_events[melody_pointer].start,
                        text=lyric_text,
                        phrase_boundary=phrase_boundary,
                    )
                )
                last_emitted_lyric = lyric_text
                melody_pointer += 1

            section_melody_count += len(melody_events)
            section_lyric_tokens += len(lyric_tokens)
            section_overflow += overflow_count
            section_orphans += orphan_extenders
            section_syllables += estimated_syllables
            bar_reports.append(
                LyricsBarAlignment(
                    section_name=section.name,
                    section_instance=section_instance.instance_number,
                    bar_index=bar_index,
                    melody_event_count=len(melody_events),
                    lyric_token_count=len(lyric_tokens),
                    overflow_count=overflow_count,
                    orphan_extenders=orphan_extenders,
                    estimated_syllables=estimated_syllables,
                    attachments=attachments,
                )
            )

        reports.append(
            LyricsSectionReport(
                section_name=section.name,
                section_instance=section_instance.instance_number,
                melody_event_count=section_melody_count,
                lyric_token_count=section_lyric_tokens,
                overflow_count=section_overflow,
                orphan_extenders=section_orphans,
                estimated_syllables=section_syllables,
                bars=bar_reports,
            )
        )
        abs_cursor += max_bars * bar_tick_count
    return reports


def collect_melody_spans(song: Song) -> List[Dict[str, Any]]:
    num, den = parse_time_signature(song)
    quantize = parse_quantize(song)
    ticks_per_beat = 480
    beats_per_bar = num * (4 / den)
    bar_tick_count = int(round(beats_per_bar * ticks_per_beat))
    grid_tick_count = grid_unit_ticks(ticks_per_beat, quantize)
    spans: List[Dict[str, Any]] = []

    abs_cursor = 0
    for section in song.sections:
        max_bars = max((len(track.bars) for track in section.tracks.values()), default=0)
        melody_track = section.tracks.get("Melody")
        if melody_track is not None:
            for bar in melody_track.bars:
                bar_start = abs_cursor + ((bar.index - 1) * bar_tick_count)
                cell_count = len(bar.cells)
                mode = melody_grid_mode(cell_count, int(round(beats_per_bar)), melody_grid_slots_per_bar(beats_per_bar, quantize))
                cell_step = melody_cell_step_ticks(mode, ticks_per_beat, grid_tick_count, bar_tick_count, cell_count)

                def add_inner(inner_tokens: List[str], inner_start: int, inner_ticks: float) -> None:
                    idx = 0
                    while idx < len(inner_tokens):
                        spec, consumed = parse_melody_event_tokens(inner_tokens, idx, 90)
                        consumed = max(1, consumed)
                        if spec is not None:
                            start_tick = inner_start + int(round(idx * inner_ticks))
                            duration = max(1, int(round(inner_ticks))) + spec.dashes * grid_tick_count
                            spans.append(
                                {
                                    "section": section.name,
                                    "track": "Melody",
                                    "bar": bar.index,
                                    "start_tick": start_tick,
                                    "end_tick": start_tick + duration,
                                    "has_bend": spec.bend_semitones is not None or spec.ramp_target_note is not None,
                                }
                            )
                        idx += consumed

                for cell_index, cell in enumerate(bar.cells):
                    if not cell.tokens:
                        continue
                    cell_start = bar_start + int(round(cell_index * cell_step))
                    if mode == "beat":
                        bracket_tokens = [token for token in cell.tokens if token_is_bracket_group(token)]
                        if bracket_tokens:
                            for token in bracket_tokens:
                                inner_tokens = parse_bracket_group(token)
                                if inner_tokens:
                                    add_inner(inner_tokens, cell_start, ticks_per_beat / len(inner_tokens))
                            continue

                    sub_count = max(1, len(cell.tokens))
                    sub_ticks = cell_step / sub_count
                    idx = 0
                    while idx < len(cell.tokens):
                        token = cell.tokens[idx]
                        slot_start = cell_start + int(round(idx * sub_ticks))
                        if token_is_bracket_group(token):
                            inner_tokens = parse_bracket_group(token)
                            if inner_tokens:
                                add_inner(inner_tokens, slot_start, sub_ticks / len(inner_tokens))
                            idx += 1
                            continue
                        spec, consumed = parse_melody_event_tokens(cell.tokens, idx, 90)
                        consumed = max(1, consumed)
                        if spec is not None:
                            duration = max(1, int(round(sub_ticks))) + spec.dashes * grid_tick_count
                            spans.append(
                                {
                                    "section": section.name,
                                    "track": "Melody",
                                    "bar": bar.index,
                                    "start_tick": slot_start,
                                    "end_tick": slot_start + duration,
                                    "has_bend": spec.bend_semitones is not None or spec.ramp_target_note is not None,
                                }
                            )
                        idx += consumed
        abs_cursor += max_bars * bar_tick_count
    return spans
