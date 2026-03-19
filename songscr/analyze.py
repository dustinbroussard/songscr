from __future__ import annotations

from collections import Counter, defaultdict
import json
import re
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .core import (
    _collect_timed_bass_events_for_bar,
    _collect_timed_melody_events_for_bar,
    _grid_unit_ticks,
    _lyrics_token_estimated_syllables,
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
    resolve_chord_range,
    resolve_chord_voicing,
    resolve_scoped_tag_value,
)
from .chords import parse_chord_symbol
from .styles import _track_has_content, expand_song_templates, resolve_style_context
from .struct import build_playback_plan

DRUM_TOKENS = ("K", "S", "H", "O", "C", "T")
DRUM_NAMES = {"K": "kick", "S": "snare", "H": "closed_hat", "O": "open_hat", "C": "crash", "T": "tom"}


def _midi_to_note_name(midi_note: int) -> str:
    names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    octave = (midi_note // 12) - 1
    return f"{names[midi_note % 12]}{octave}"


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _round2(value: float) -> float:
    return round(value + 1e-9, 2)


def _cells_for_take(bar, take_number: int):
    return [SimpleNamespace(tokens=_tokens_for_take(cell.tokens, take_number), tags=cell.tags) for cell in bar.cells]


def _bar_token_string(bar, take_number: int) -> str:
    parts: List[str] = []
    for cell in bar.cells:
        filtered = _tokens_for_take(cell.tokens, take_number)
        parts.append(" ".join(filtered))
    return " | ".join(parts).strip()


def _collect_melody_specs_and_rests(bar_cells) -> Tuple[List[Any], int]:
    specs: List[Any] = []
    rest_count = 0

    def process_tokens(tokens: List[str]) -> None:
        nonlocal rest_count
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            if token in ("R", "(R)"):
                rest_count += 1
                idx += 1
                continue
            spec, consumed = _parse_melody_event_tokens(tokens, idx, 90)
            specs.append(spec) if spec is not None else None
            idx += max(1, consumed)

    for cell in bar_cells:
        if not cell.tokens:
            continue
        if len(cell.tokens) == 1 and _token_is_bracket_group(cell.tokens[0]):
            process_tokens(_parse_bracket_group(cell.tokens[0]))
            continue
        tokens = []
        for token in cell.tokens:
            if _token_is_bracket_group(token):
                process_tokens(_parse_bracket_group(token))
            else:
                tokens.append(token)
        if tokens:
            process_tokens(tokens)
    return [spec for spec in specs if spec is not None], rest_count


def _collect_drum_hits_for_bar(bar_cells) -> Counter:
    counts: Counter = Counter()

    def add_token(token: str) -> None:
        if token in DRUM_TOKENS:
            counts[token] += 1

    for cell in bar_cells:
        for token in cell.tokens:
            if _token_is_bracket_group(token):
                for inner in _parse_bracket_group(token):
                    add_token(inner)
            else:
                add_token(token)
    return counts


def _section_labels(song) -> Dict[int, List[str]]:
    labels: Dict[int, List[str]] = defaultdict(list)
    for item in song.struct_items:
        if item.get("type") != "label":
            continue
        section_index = item.get("section_index")
        if isinstance(section_index, int):
            labels[section_index].append(str(item.get("name", "")).strip())
    return {idx: sorted(values) for idx, values in labels.items()}


def _normalized_chord_token(token: str) -> str:
    text = _strip_paren_dyn(token)
    text = re.sub(r"\{[^}]+\}", "", text).strip()
    return text


def _template_generation_flags(source_song, expanded_song, section_index: int) -> Dict[str, bool]:
    source_section = source_song.sections[section_index]
    expanded_section = expanded_song.sections[section_index]
    source_bass = _track_has_content(source_section.tracks.get("Bass"))
    source_drums = _track_has_content(source_section.tracks.get("Drums"))
    expanded_bass = _track_has_content(expanded_section.tracks.get("Bass"))
    expanded_drums = _track_has_content(expanded_section.tracks.get("Drums"))
    return {
        "bass_generated": expanded_bass and not source_bass,
        "drums_generated": expanded_drums and not source_drums,
        "bass_explicit": source_bass,
        "drums_explicit": source_drums,
    }


def analyze_song(text: str) -> Dict[str, Any]:
    source_song = parse_song(text)
    expanded_song = expand_song_templates(parse_song(text))
    playback_plan, struct_issues = build_playback_plan(expanded_song)
    lint_issues = lint_song(text)
    all_issues = list(lint_issues)
    for issue in struct_issues:
        all_issues.append(
            SimpleNamespace(level=issue.level, rule=issue.rule, message=issue.message, token=issue.token, expected=issue.expected)
        )

    tempo = _parse_tempo(expanded_song)
    num, den = _parse_time_signature(expanded_song)
    quantize = _parse_quantize(expanded_song)
    beats_per_bar = num * (4 / den)
    ppq = 480
    ticks_per_beat = ppq
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))
    grid_unit_ticks = _grid_unit_ticks(ticks_per_beat, quantize)

    labels_by_section = _section_labels(source_song)
    repeated_instances = sum(1 for item in playback_plan if item.instance_number > 1)
    total_rendered_bars = 0
    playback_instances: List[Dict[str, Any]] = []
    source_track_names = sorted({track.name for section in source_song.sections for track in section.tracks.values()})
    expanded_track_names = sorted({track.name for section in expanded_song.sections for track in section.tracks.values()})

    section_playback_bars: Counter = Counter()
    melody_events_total = 0
    melody_pitch_set = set()
    melody_low: Optional[int] = None
    melody_high: Optional[int] = None
    melody_high_by_section: Dict[str, int] = {}
    melody_density_by_section: Counter = Counter()
    melody_bar_counts_by_section: Counter = Counter()
    melody_rests = 0
    melody_bends = 0
    melody_ramps = 0
    melody_vibrato = 0
    longest_melody_duration_ticks = 0

    bass_events_total = 0
    bass_generated = False
    bass_explicit = False
    bass_pattern_names = set()
    bass_pitch_set = set()
    bass_low: Optional[int] = None
    bass_high: Optional[int] = None
    bass_bars_total = 0
    previous_bass_note: Optional[int] = None
    repeated_pedal_notes = 0

    chord_events_total = 0
    unique_chords = set()
    slash_chords = 0
    guitar_voicing_usage = 0
    chord_changes_per_bar: List[int] = []
    voicing_modes = set()
    chord_range_values = set()

    drum_hits_total = 0
    drum_piece_counts: Counter = Counter()
    drums_generated = False
    drums_explicit = False
    drum_bars_total = 0

    lyric_report = build_lyrics_alignment_report(expanded_song)
    lyrics_tokens_total = sum(section.lyric_token_count for section in lyric_report)
    lyrics_syllables_total = sum(section.estimated_syllables for section in lyric_report)
    lyrics_overflow_total = sum(section.overflow_count for section in lyric_report)
    lyrics_orphan_total = sum(section.orphan_extenders for section in lyric_report)
    lyric_tokens_by_section: Counter = Counter()
    for section in lyric_report:
        lyric_tokens_by_section[section.section_name] += section.lyric_token_count

    abs_cursor = 0
    for plan_index, section_instance in enumerate(playback_plan):
        source_section = source_song.sections[section_instance.section_index]
        expanded_section = expanded_song.sections[section_instance.section_index]
        tracks = expanded_section.tracks
        max_bars = max((len(track.bars) for track in tracks.values()), default=0)
        total_rendered_bars += max_bars
        section_playback_bars[expanded_section.name] += max_bars
        playback_instances.append(
            {
                "order": plan_index + 1,
                "section": expanded_section.name,
                "section_index": section_instance.section_index,
                "instance": section_instance.instance_number,
                "take": section_instance.take_number,
                "bars": max_bars,
            }
        )

        generation_flags = _template_generation_flags(source_song, expanded_song, section_instance.section_index)
        bass_generated = bass_generated or generation_flags["bass_generated"]
        drums_generated = drums_generated or generation_flags["drums_generated"]
        bass_explicit = bass_explicit or generation_flags["bass_explicit"]
        drums_explicit = drums_explicit or generation_flags["drums_explicit"]
        if generation_flags["bass_generated"]:
            context = resolve_style_context(source_song, source_section, track_name="Bass")
            if context.bass_pattern:
                bass_pattern_names.add(context.bass_pattern)

        melody_track = tracks.get("Melody")
        if melody_track is not None:
            for bar_offset, bar in enumerate(melody_track.bars):
                filtered_cells = _cells_for_take(bar, section_instance.take_number)
                bar_start = abs_cursor + (bar_offset * bar_ticks)
                events = _collect_timed_melody_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_unit_ticks,
                    bar_start,
                    section_name=expanded_section.name,
                    section_instance=section_instance.instance_number,
                    bar_index=bar.index,
                )
                specs, rest_count = _collect_melody_specs_and_rests(filtered_cells)
                melody_rests += rest_count
                melody_events_total += len(events)
                melody_density_by_section[expanded_section.name] += len(events)
                melody_bar_counts_by_section[expanded_section.name] += 1
                for event in events:
                    melody_pitch_set.add(event.midi_note)
                    melody_low = event.midi_note if melody_low is None else min(melody_low, event.midi_note)
                    melody_high = event.midi_note if melody_high is None else max(melody_high, event.midi_note)
                    current_high = melody_high_by_section.get(expanded_section.name)
                    if current_high is None or event.midi_note > current_high:
                        melody_high_by_section[expanded_section.name] = event.midi_note
                    longest_melody_duration_ticks = max(longest_melody_duration_ticks, event.duration)
                for spec in specs:
                    if spec.bend_semitones is not None:
                        melody_bends += 1
                    if spec.ramp_target_note is not None:
                        melody_ramps += 1
                    if spec.vibrato_depth is not None:
                        melody_vibrato += 1

        bass_track = tracks.get("Bass")
        if bass_track is not None:
            for bar_offset, bar in enumerate(bass_track.bars):
                filtered_cells = _cells_for_take(bar, section_instance.take_number)
                events, _ = _collect_timed_bass_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_unit_ticks,
                    abs_cursor + (bar_offset * bar_ticks),
                    section_name=expanded_section.name,
                    section_instance=section_instance.instance_number,
                    bar_index=bar.index,
                )
                bass_bars_total += 1
                bass_events_total += len(events)
                for event in events:
                    bass_pitch_set.add(event.midi_note)
                    bass_low = event.midi_note if bass_low is None else min(bass_low, event.midi_note)
                    bass_high = event.midi_note if bass_high is None else max(bass_high, event.midi_note)
                    if previous_bass_note is not None and previous_bass_note == event.midi_note:
                        repeated_pedal_notes += 1
                    previous_bass_note = event.midi_note

        chord_track = tracks.get("Chords") or tracks.get("Chord")
        if chord_track is not None:
            for bar in chord_track.bars:
                filtered_cells = _cells_for_take(bar, section_instance.take_number)
                chord_changes = 0
                for cell in filtered_cells:
                    if not cell.tokens:
                        continue
                    token = cell.tokens[0]
                    normalized = _normalized_chord_token(token)
                    if normalized in ("", "%", "R", "(R)"):
                        continue
                    spec = parse_chord_symbol(normalized)
                    if spec is None:
                        continue
                    chord_events_total += 1
                    chord_changes += 1
                    unique_chords.add(normalized)
                    if spec.slash_bass:
                        slash_chords += 1
                    use_guitar = "{" in token or resolve_chord_voicing(expanded_song, section_instance.section_index, chord_track, bar) == "guitar"
                    if use_guitar:
                        guitar_voicing_usage += 1
                    voicing_modes.add(resolve_chord_voicing(expanded_song, section_instance.section_index, chord_track, bar))
                    chord_range_value = resolve_scoped_tag_value(expanded_song, section_instance.section_index, chord_track, bar, "chord range")
                    if chord_range_value:
                        chord_range_values.add(str(chord_range_value))
                chord_changes_per_bar.append(chord_changes)

        drums_track = tracks.get("Drums")
        if drums_track is not None:
            for bar in drums_track.bars:
                filtered_cells = _cells_for_take(bar, section_instance.take_number)
                counts = _collect_drum_hits_for_bar(filtered_cells)
                drum_bars_total += 1
                for token, count in counts.items():
                    drum_piece_counts[token] += count
                    drum_hits_total += count

        abs_cursor += max_bars * bar_ticks

    sections_summary: List[Dict[str, Any]] = []
    repetition_scores: Dict[str, float] = {}
    for section_index, section in enumerate(source_song.sections):
        expanded_section = expanded_song.sections[section_index]
        bar_counts = {track_name: len(track.bars) for track_name, track in expanded_section.tracks.items()}
        context = resolve_style_context(source_song, section, track_name="Bass")
        generation_flags = _template_generation_flags(source_song, expanded_song, section_index)
        style_tags = {
            "style": context.style,
            "drum_pattern": context.drum_pattern,
            "bass_pattern": context.bass_pattern,
            "template_mode": context.template_mode,
        }
        normalized_bars: List[str] = []
        for track_name in ("Chords", "Melody"):
            track = expanded_section.tracks.get(track_name)
            if track is None:
                continue
            for bar in track.bars:
                normalized_bars.append(f"{track_name}:{_bar_token_string(bar, 1)}")
        repetition_value = 0.0
        if normalized_bars:
            counts = Counter(normalized_bars)
            repeated = sum(count for count in counts.values() if count > 1)
            repetition_value = _round2(_safe_div(repeated, len(normalized_bars)))
        repetition_scores[section.name] = repetition_value
        sections_summary.append(
            {
                "name": section.name,
                "bar_counts_per_track": dict(sorted(bar_counts.items())),
                "has_chords": _track_has_content(expanded_section.tracks.get("Chords") or expanded_section.tracks.get("Chord")),
                "has_melody": _track_has_content(expanded_section.tracks.get("Melody")),
                "has_bass": _track_has_content(expanded_section.tracks.get("Bass")),
                "has_drums": _track_has_content(expanded_section.tracks.get("Drums")),
                "has_lyrics": _track_has_content(expanded_section.tracks.get("Lyrics")),
                "style_context": style_tags,
                "labels": labels_by_section.get(section_index, []),
                "generated_tracks": [name for name, enabled in (("Bass", generation_flags["bass_generated"]), ("Drums", generation_flags["drums_generated"])) if enabled],
            }
        )

    warning_counts = Counter(getattr(issue, "rule", "unknown") or "unknown" for issue in all_issues)
    warning_rules = sorted(rule for rule in warning_counts.keys() if rule)

    energy_by_section: Dict[str, float] = {}
    for section in sections_summary:
        name = section["name"]
        bars = section_playback_bars.get(name, 0)
        melody_density = _safe_div(melody_density_by_section.get(name, 0), bars)
        lyric_density = _safe_div(lyric_tokens_by_section.get(name, 0), bars)
        chord_density = _safe_div(sum(item["bars"] for item in playback_instances if item["section"] == name), bars)
        drum_density = _safe_div(drum_hits_total, total_rendered_bars)
        energy_by_section[name] = _round2(melody_density + lyric_density + chord_density + drum_density)

    hook_candidate = None
    if energy_by_section:
        hook_candidate = max(
            sections_summary,
            key=lambda section: (repetition_scores.get(section["name"], 0.0) + energy_by_section.get(section["name"], 0.0), section["name"]),
        )["name"]

    analysis = {
        "global": {
            "tempo": tempo,
            "time_signature": f"{num}/{den}",
            "quantize": quantize,
            "section_count": len(source_song.sections),
            "playback_section_instance_count": len(playback_plan),
            "total_rendered_bars": total_rendered_bars,
            "track_names_present": expanded_track_names,
            "source_track_names_present": source_track_names,
            "templates_expanded_any_tracks": bass_generated or drums_generated,
            "bass_generated": bass_generated,
            "bass_explicit": bass_explicit,
            "drums_generated": drums_generated,
            "drums_explicit": drums_explicit,
        },
        "sections": sections_summary,
        "playback": {
            "instances": playback_instances,
            "total_bars_rendered": total_rendered_bars,
            "repeated_section_instances": repeated_instances,
        },
        "melody": {
            "total_note_events": melody_events_total,
            "pitch_range": {
                "lowest": _midi_to_note_name(melody_low) if melody_low is not None else None,
                "highest": _midi_to_note_name(melody_high) if melody_high is not None else None,
            },
            "unique_pitch_count": len(melody_pitch_set),
            "average_note_density_per_bar": _round2(_safe_div(melody_events_total, total_rendered_bars)),
            "note_density_per_section": {name: _round2(_safe_div(count, melody_bar_counts_by_section.get(name, 0))) for name, count in sorted(melody_density_by_section.items())},
            "highest_note_by_section": {name: _midi_to_note_name(note) for name, note in sorted(melody_high_by_section.items())},
            "longest_sustained_note_beats": _round2(_safe_div(longest_melody_duration_ticks, ticks_per_beat)),
            "rest_count": melody_rests,
            "expressive_events": {"bends": melody_bends, "ramps": melody_ramps, "vibrato": melody_vibrato},
        },
        "bass": {
            "total_note_events": bass_events_total,
            "explicit": bass_explicit,
            "generated": bass_generated,
            "pitch_range": {
                "lowest": _midi_to_note_name(bass_low) if bass_low is not None else None,
                "highest": _midi_to_note_name(bass_high) if bass_high is not None else None,
            },
            "average_density_per_bar": _round2(_safe_div(bass_events_total, bass_bars_total)),
            "repeated_pedal_notes": repeated_pedal_notes,
            "generated_pattern_names": sorted(bass_pattern_names),
        },
        "chords": {
            "total_chord_events": chord_events_total,
            "unique_chord_symbols_count": len(unique_chords),
            "chord_changes_per_bar": chord_changes_per_bar,
            "slash_chord_count": slash_chords,
            "guitar_voicing_usage_count": guitar_voicing_usage,
            "voicing_modes_used": sorted(mode for mode in voicing_modes if mode),
            "chord_ranges_used": sorted(chord_range_values),
            "average_chord_changes_per_bar": _round2(_safe_div(sum(chord_changes_per_bar), len(chord_changes_per_bar))),
        },
        "drums": {
            "total_hit_count": drum_hits_total,
            "piece_counts": {DRUM_NAMES[token]: drum_piece_counts.get(token, 0) for token in DRUM_TOKENS},
            "average_hits_per_bar": _round2(_safe_div(drum_hits_total, drum_bars_total)),
            "generated": drums_generated,
            "explicit": drums_explicit,
        },
        "lyrics": {
            "total_lyric_tokens": lyrics_tokens_total,
            "estimated_syllables": lyrics_syllables_total,
            "lyric_tokens_per_section": dict(sorted(lyric_tokens_by_section.items())),
            "overflow_warning_count": lyrics_overflow_total,
            "orphan_extender_warning_count": lyrics_orphan_total,
            "average_lyric_syllables_per_melody_note": _round2(_safe_div(lyrics_syllables_total, melody_events_total)),
            "average_lyric_tokens_per_bar": _round2(_safe_div(lyrics_tokens_total, total_rendered_bars)),
        },
        "warnings": {
            "count_by_rule": dict(sorted(warning_counts.items())),
            "unique_rules": warning_rules,
        },
        "heuristics": {
            "energy_proxy_by_section": dict(sorted(energy_by_section.items())),
            "repetition_score_by_section": dict(sorted(repetition_scores.items())),
            "hook_candidate": hook_candidate,
        },
    }
    return analysis


def format_analysis_text(analysis: Dict[str, Any]) -> str:
    lines: List[str] = []

    def add_header(title: str) -> None:
        if lines:
            lines.append("")
        lines.append(title)

    global_summary = analysis["global"]
    add_header("Global")
    lines.append(f"tempo: {global_summary['tempo']}")
    lines.append(f"time_signature: {global_summary['time_signature']}")
    lines.append(f"quantize: {global_summary['quantize']}")
    lines.append(f"section_count: {global_summary['section_count']}")
    lines.append(f"playback_section_instance_count: {global_summary['playback_section_instance_count']}")
    lines.append(f"total_rendered_bars: {global_summary['total_rendered_bars']}")
    lines.append(f"track_names_present: {', '.join(global_summary['track_names_present']) or '-'}")
    lines.append(f"templates_expanded_any_tracks: {global_summary['templates_expanded_any_tracks']}")
    lines.append(
        f"bass: explicit={global_summary['bass_explicit']} generated={global_summary['bass_generated']} | "
        f"drums: explicit={global_summary['drums_explicit']} generated={global_summary['drums_generated']}"
    )

    add_header("Playback")
    lines.append(f"total_bars_rendered: {analysis['playback']['total_bars_rendered']}")
    lines.append(f"repeated_section_instances: {analysis['playback']['repeated_section_instances']}")
    for item in analysis["playback"]["instances"]:
        lines.append(
            f"- {item['order']}: {item['section']}#{item['instance']} take={item['take']} bars={item['bars']}"
        )

    add_header("Sections")
    for section in analysis["sections"]:
        style_context = section["style_context"]
        lines.append(
            f"- {section['name']}: bars={section['bar_counts_per_track']} "
            f"tracks=chords:{section['has_chords']} melody:{section['has_melody']} bass:{section['has_bass']} "
            f"drums:{section['has_drums']} lyrics:{section['has_lyrics']}"
        )
        lines.append(
            f"  style={style_context['style']} drum_pattern={style_context['drum_pattern']} "
            f"bass_pattern={style_context['bass_pattern']} labels={','.join(section['labels']) or '-'} "
            f"generated={','.join(section['generated_tracks']) or '-'}"
        )

    add_header("Melody")
    melody = analysis["melody"]
    lines.append(f"total_note_events: {melody['total_note_events']}")
    lines.append(f"pitch_range: {melody['pitch_range']['lowest']} .. {melody['pitch_range']['highest']}")
    lines.append(f"unique_pitch_count: {melody['unique_pitch_count']}")
    lines.append(f"average_note_density_per_bar: {melody['average_note_density_per_bar']}")
    lines.append(f"note_density_per_section: {json.dumps(melody['note_density_per_section'], sort_keys=True)}")
    lines.append(f"highest_note_by_section: {json.dumps(melody['highest_note_by_section'], sort_keys=True)}")
    lines.append(f"longest_sustained_note_beats: {melody['longest_sustained_note_beats']}")
    lines.append(f"rest_count: {melody['rest_count']}")
    lines.append(f"expressive_events: {json.dumps(melody['expressive_events'], sort_keys=True)}")

    add_header("Bass")
    bass = analysis["bass"]
    lines.append(f"total_note_events: {bass['total_note_events']}")
    lines.append(f"explicit: {bass['explicit']} generated: {bass['generated']}")
    lines.append(f"pitch_range: {bass['pitch_range']['lowest']} .. {bass['pitch_range']['highest']}")
    lines.append(f"average_density_per_bar: {bass['average_density_per_bar']}")
    lines.append(f"repeated_pedal_notes: {bass['repeated_pedal_notes']}")
    lines.append(f"generated_pattern_names: {', '.join(bass['generated_pattern_names']) or '-'}")

    add_header("Chords")
    chords = analysis["chords"]
    lines.append(f"total_chord_events: {chords['total_chord_events']}")
    lines.append(f"unique_chord_symbols_count: {chords['unique_chord_symbols_count']}")
    lines.append(f"average_chord_changes_per_bar: {chords['average_chord_changes_per_bar']}")
    lines.append(f"slash_chord_count: {chords['slash_chord_count']}")
    lines.append(f"guitar_voicing_usage_count: {chords['guitar_voicing_usage_count']}")
    lines.append(f"voicing_modes_used: {', '.join(chords['voicing_modes_used']) or '-'}")
    lines.append(f"chord_ranges_used: {', '.join(chords['chord_ranges_used']) or '-'}")

    add_header("Drums")
    drums = analysis["drums"]
    lines.append(f"total_hit_count: {drums['total_hit_count']}")
    lines.append(f"piece_counts: {json.dumps(drums['piece_counts'], sort_keys=True)}")
    lines.append(f"average_hits_per_bar: {drums['average_hits_per_bar']}")
    lines.append(f"explicit: {drums['explicit']} generated: {drums['generated']}")

    add_header("Lyrics")
    lyrics = analysis["lyrics"]
    lines.append(f"total_lyric_tokens: {lyrics['total_lyric_tokens']}")
    lines.append(f"estimated_syllables: {lyrics['estimated_syllables']}")
    lines.append(f"lyric_tokens_per_section: {json.dumps(lyrics['lyric_tokens_per_section'], sort_keys=True)}")
    lines.append(f"overflow_warning_count: {lyrics['overflow_warning_count']}")
    lines.append(f"orphan_extender_warning_count: {lyrics['orphan_extender_warning_count']}")
    lines.append(f"average_lyric_syllables_per_melody_note: {lyrics['average_lyric_syllables_per_melody_note']}")
    lines.append(f"average_lyric_tokens_per_bar: {lyrics['average_lyric_tokens_per_bar']}")

    add_header("Warnings")
    warnings = analysis["warnings"]
    lines.append(f"unique_rules: {', '.join(warnings['unique_rules']) or '-'}")
    lines.append(f"count_by_rule: {json.dumps(warnings['count_by_rule'], sort_keys=True)}")

    add_header("Heuristics")
    heuristics = analysis["heuristics"]
    lines.append(f"energy_proxy_by_section: {json.dumps(heuristics['energy_proxy_by_section'], sort_keys=True)}")
    lines.append(f"repetition_score_by_section: {json.dumps(heuristics['repetition_score_by_section'], sort_keys=True)}")
    lines.append(f"hook_candidate: {heuristics['hook_candidate']}")

    return "\n".join(lines) + "\n"
