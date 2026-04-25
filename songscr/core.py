from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
import re

from .bass import generate_bass_events_from_chords
from .lint import LintIssue, lint_song
from .lyrics import (
    AlignedLyricEvent,
    LyricsBarAlignment,
    LyricsSectionReport,
    build_lyrics_alignment_report,
    collect_lyric_tokens_for_bar,
    collect_melody_spans,
    lyric_token_payload,
    lyrics_token_estimated_syllables,
)
from .parser import (
    ALT_ENDING_TOKEN_RE,
    KNOWN_TAGS,
    TAG_RE,
    TRACK_ALIASES,
    emit_song,
    extract_bar_tags_and_strip,
    filter_tokens_for_take,
    format_song,
    parse_song,
    parse_tags_from_line,
    split_bar_cells,
    tokenize_cell,
)
from .song_settings import resolve_bass_octave, resolve_bass_pattern, resolve_bass_rhythm
from .struct import build_playback_plan
from .timing import (
    CHORD_RE,
    NOTE_RE,
    MelodyEventSpec,
    TimedBassEvent,
    TimedMelodyEvent,
    bar_tags,
    collect_timed_bass_events_for_bar,
    collect_timed_melody_events_for_bar,
    drum_token_valid,
    dyn_to_velocity,
    extract_velocity,
    find_last_tag_value,
    grid_unit_ticks,
    has_explicit_pitch_bend_range,
    melody_cell_step_ticks,
    melody_grid_mode,
    melody_grid_slots_per_bar,
    melody_token_is_note_or_rest,
    note_to_midi,
    parse_bracket_group,
    parse_melody_event_tokens,
    parse_melody_note_expression,
    parse_melody_note_token,
    parse_pitch_bend_range_value,
    parse_quantize,
    parse_tempo,
    parse_time_signature,
    resolve_capo,
    resolve_chord_range,
    resolve_chord_voicing,
    resolve_guitar_position,
    resolve_guitar_tuning,
    resolve_pitch_bend_range,
    resolve_scoped_tag_value,
    resolve_voice_leading,
    strip_dyn_if_present,
    strip_paren_dyn,
    token_is_bracket_group,
)


def song_stats(text: str) -> Dict[str, Any]:
    from .styles import expand_song_templates

    source_song = parse_song(text)
    song = expand_song_templates(parse_song(text))
    num, den = parse_time_signature(song)
    quantize = parse_quantize(song)
    beats_per_bar = num * (4 / den)
    ticks_per_beat = 480
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))
    grid_ticks = grid_unit_ticks(ticks_per_beat, quantize)

    track_names = set()
    bars_total = 0
    melody_note_events = 0
    drum_hits = 0
    for section in song.sections:
        for track in section.tracks.values():
            track_names.add(track.name)
            bars_total += len(track.bars)
            for bar in track.bars:
                for cell in bar.cells:
                    for token in cell.tokens:
                        if track.name.lower() == "melody":
                            normalized = strip_paren_dyn(token)
                            normalized = re.sub(r"[-^]+$", "", normalized)
                            normalized = re.sub(r"(v\d+|b\d+|v|b)$", "", normalized)
                            if normalized in ("R", "(R)", "%", ">>"):
                                continue
                            if normalized.startswith("~"):
                                normalized = normalized[1:]
                            if "/" in normalized:
                                normalized = normalized.split("/", 1)[0]
                            if normalized.startswith("(") and normalized.endswith(")"):
                                normalized = normalized[1:-1]
                            if note_to_midi(normalized) is not None:
                                melody_note_events += 1
                        if track.name.lower() == "drums" and token in ("K", "S", "H", "O", "C", "T"):
                            drum_hits += 1

    playback_plan, _ = build_playback_plan(song)
    source_playback_plan, _ = build_playback_plan(source_song)
    bass_note_events = 0
    bass_explicit = False
    bass_generated = False
    source_abs_cursor = 0
    source_explicit_bass_counts = []
    for section_instance in source_playback_plan:
        section = source_song.sections[section_instance.section_index]
        max_bars = max((len(track.bars) for track in section.tracks.values()), default=0)
        bass_track = section.tracks.get("Bass")
        explicit_events = 0
        if bass_track is not None:
            for bar_index, bar in enumerate(bass_track.bars):
                filtered_cells = [SimpleNamespace(tokens=filter_tokens_for_take(cell.tokens, section_instance.take_number)) for cell in bar.cells]
                bass_events, _ = collect_timed_bass_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_ticks,
                    source_abs_cursor + (bar_index * bar_ticks),
                )
                explicit_events += len(bass_events)
        source_explicit_bass_counts.append(explicit_events)
        source_abs_cursor += max_bars * bar_ticks

    abs_cursor = 0
    for plan_index, section_instance in enumerate(playback_plan):
        section = song.sections[section_instance.section_index]
        max_bars = max((len(track.bars) for track in section.tracks.values()), default=0)
        bass_track = section.tracks.get("Bass")
        explicit_events = 0
        if bass_track is not None:
            for bar_index, bar in enumerate(bass_track.bars):
                filtered_cells = [SimpleNamespace(tokens=filter_tokens_for_take(cell.tokens, section_instance.take_number)) for cell in bar.cells]
                bass_events, _ = collect_timed_bass_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_ticks,
                    abs_cursor + (bar_index * bar_ticks),
                )
                explicit_events += len(bass_events)
        if explicit_events > 0:
            bass_note_events += explicit_events
            if plan_index < len(source_explicit_bass_counts) and source_explicit_bass_counts[plan_index] > 0:
                bass_explicit = True
            else:
                bass_generated = True
        else:
            pattern = resolve_bass_pattern(song, section_instance, bass_track)
            chords_track = section.tracks.get("Chords") or section.tracks.get("Chord")
            if pattern is not None and chords_track is not None:
                generated = generate_bass_events_from_chords(
                    section,
                    pattern,
                    resolve_bass_rhythm(song, section_instance, bass_track),
                    resolve_bass_octave(song, section_instance, bass_track),
                    {
                        "take_number": section_instance.take_number,
                        "abs_cursor": abs_cursor,
                        "bar_duration": bar_ticks,
                        "beat_duration": ticks_per_beat,
                        "beats_per_bar": int(round(beats_per_bar)),
                        "max_bars": max_bars,
                    },
                )
                if generated:
                    bass_generated = True
                    bass_note_events += len(generated)
        abs_cursor += max_bars * bar_ticks

    return {
        "tempo": song.meta.get("tempo", 120),
        "time_signature": song.meta.get("time signature", "4/4"),
        "sections": len(song.sections),
        "tracks": sorted(track_names),
        "bars_total": bars_total,
        "melody_note_events": melody_note_events,
        "bass_note_events": bass_note_events,
        "bass_explicit": bass_explicit,
        "bass_generated": bass_generated,
        "drum_hits": drum_hits,
    }


_split_bar_cells = split_bar_cells
_tokens_for_take = filter_tokens_for_take
_extract_bar_tags_and_strip = extract_bar_tags_and_strip
_tokenize_cell = tokenize_cell
_note_to_midi = note_to_midi
_parse_quantize = parse_quantize
_grid_unit_ticks = grid_unit_ticks
_melody_token_is_note_or_rest = melody_token_is_note_or_rest
_parse_pitch_bend_range_value = parse_pitch_bend_range_value
_find_last_tag_value = find_last_tag_value
_bar_tags = bar_tags
_parse_time_signature = parse_time_signature
_parse_tempo = parse_tempo
_dyn_to_vel = dyn_to_velocity
_extract_velocity = extract_velocity
_strip_paren_dyn = strip_paren_dyn
_strip_dyn_if_present = strip_dyn_if_present
_parse_melody_note_expression = parse_melody_note_expression
_parse_melody_note_token = parse_melody_note_token
_parse_melody_event_tokens = parse_melody_event_tokens
_token_is_bracket_group = token_is_bracket_group
_parse_bracket_group = parse_bracket_group
_drum_token_valid = drum_token_valid
_melody_grid_slots_per_bar = melody_grid_slots_per_bar
_melody_grid_mode = melody_grid_mode
_melody_cell_step_ticks = melody_cell_step_ticks
_lyrics_token_estimated_syllables = lyrics_token_estimated_syllables
_lyric_token_payload = lyric_token_payload
_collect_timed_melody_events_for_bar = collect_timed_melody_events_for_bar
_collect_timed_bass_events_for_bar = collect_timed_bass_events_for_bar
_collect_lyric_tokens_for_bar = collect_lyric_tokens_for_bar
_collect_melody_spans = collect_melody_spans
