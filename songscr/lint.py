from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import re

from .ast import Song, Tag
from .automation import auto_param_to_cc, parse_auto
from .chords import parse_chord_symbol
from .guitar_voicings import generate_guitar_voicing_details, parse_guitar_tuning, parse_string_set_suffix
from .lyrics import build_lyrics_alignment_report, collect_melody_spans
from .parser import ALT_ENDING_TOKEN_RE, KNOWN_TAGS, parse_song
from .song_settings import resolve_bass_pattern
from .struct import build_playback_plan
from .timing import (
    CHORD_RE,
    NOTE_RE,
    collect_timed_bass_events_for_bar,
    drum_token_valid,
    extract_velocity,
    find_last_tag_value,
    grid_unit_ticks,
    melody_token_is_note_or_rest,
    note_to_midi,
    parse_bracket_group,
    parse_melody_event_tokens,
    parse_melody_note_expression,
    parse_pitch_bend_range_value,
    parse_quantize,
    parse_time_signature,
    resolve_capo,
    resolve_chord_range,
    resolve_chord_voicing,
    resolve_guitar_position,
    resolve_guitar_tuning,
    resolve_pitch_bend_range,
    resolve_voice_leading,
    token_is_bracket_group,
)


@dataclass
class LintIssue:
    level: str
    message: str
    section: Optional[str] = None
    track: Optional[str] = None
    bar: Optional[int] = None
    beat: Optional[int] = None
    token: Optional[str] = None
    rule: Optional[str] = None
    expected: Optional[str] = None

    def format_line(self, file: Optional[str] = None) -> str:
        parts = [self.level]
        if file:
            parts.append(f"file={file}")
        if self.section is not None:
            parts.append(f"section={self.section}")
        if self.track is not None:
            parts.append(f"track={self.track}")
        if self.bar is not None:
            parts.append(f"bar={self.bar}")
        if self.beat is not None:
            parts.append(f"beat={self.beat}")
        if self.token is not None:
            parts.append(f'token="{self.token}"')
        if self.rule is not None:
            parts.append(f'rule="{self.rule}"')
        if self.expected is not None:
            parts.append(f'expected="{self.expected}"')
        parts.append(self.message)
        return " ".join(parts)


def lint_song(text: str, filename: Optional[str] = None, strict: bool = False, song: Optional[Song] = None) -> List[LintIssue]:
    song = song or parse_song(text)
    issues: List[LintIssue] = []

    def warn(message: str, **kw: Any) -> None:
        issues.append(LintIssue("WARN", message, **kw))

    def err(message: str, **kw: Any) -> None:
        issues.append(LintIssue("ERROR", message, **kw))

    def check_tags(tag_list: List[Tag], scope_info: Dict[str, Any]) -> None:
        for tag in tag_list:
            tag_name = tag.name.lower().strip()
            if tag_name not in KNOWN_TAGS:
                (err if strict else warn)(
                    f"Unknown tag [{tag.name}] preserved.",
                    **scope_info,
                    token=f"[{tag.name}]",
                    rule="unknownTag",
                    expected="known tag or --lenient",
                )
            if tag_name == "auto":
                if tag.value is None:
                    err("Malformed Auto ramp syntax.", **scope_info, token=f"[Auto: {tag.value or ''}]", rule="autoSyntax", expected="Auto: Param 0%->100%")
                    continue
                try:
                    param, _, _ = parse_auto(tag.value)
                except ValueError:
                    err("Malformed Auto ramp syntax.", **scope_info, token=f"[Auto: {tag.value or ''}]", rule="autoSyntax", expected="Auto: Param 0%->100%")
                    continue
                if auto_param_to_cc(param) is None:
                    warn("Unknown Auto parameter.", **scope_info, token=f"[Auto: {tag.value}]", rule="autoParamUnknown", expected="reverb chorus filter_cutoff cutoff filter expression volume pan")
            if tag_name == "pitch bend range":
                parsed_range = parse_pitch_bend_range_value(tag.value)
                if parsed_range is None or not 1 <= parsed_range <= 24:
                    err("Pitch bend range must be an integer within 1..24 semitones.", **scope_info, token=f"[Pitch Bend Range: {tag.value or ''}]", rule="bendRange", expected="integer semitones within 1..24")
            if tag_name == "style":
                style_value = str(tag.value or "").strip().lower()
                if style_value and style_value not in ("slowblues", "straightrock", "funklite"):
                    warn("Unknown Style value.", **scope_info, token=f"[Style: {tag.value or ''}]", rule="styleUnknown", expected="SlowBlues StraightRock FunkLite")
            if tag_name == "drum pattern":
                drum_value = str(tag.value or "").strip().lower()
                if drum_value and drum_value not in ("halftimeshuffle", "straight8rock", "fouronfloor", "funk16kick", "balladsparse"):
                    warn("Unknown Drum Pattern value.", **scope_info, token=f"[Drum Pattern: {tag.value or ''}]", rule="drumPatternUnknown", expected="HalfTimeShuffle Straight8Rock FourOnFloor Funk16Kick BalladSparse")
            if tag_name == "bass pattern":
                bass_value = str(tag.value or "").strip().lower()
                if bass_value and bass_value not in ("root", "root5", "octave", "walkup", "pedal"):
                    warn("Unknown Bass Pattern value.", **scope_info, token=f"[Bass Pattern: {tag.value or ''}]", rule="bassPatternUnknown", expected="Root Root5 Octave WalkUp Pedal")
            if tag_name == "guitar tuning":
                if tag.value is None:
                    warn("Invalid Guitar Tuning value; using standard tuning.", **scope_info, token="[Guitar Tuning]", rule="guitarTuning", expected="six note names like E2 A2 D3 G3 B3 E4")
                    continue
                try:
                    parse_guitar_tuning(tag.value)
                except ValueError:
                    warn("Invalid Guitar Tuning value; using standard tuning.", **scope_info, token=f"[Guitar Tuning: {tag.value}]", rule="guitarTuning", expected="six note names like E2 A2 D3 G3 B3 E4")

    check_tags(song.tags, {"section": None, "track": None})
    for section in song.sections:
        check_tags(section.tags, {"section": section.name, "track": None})
        for track in section.tracks.values():
            check_tags(track.tags, {"section": section.name, "track": track.name})
            for bar in track.bars:
                for cell in bar.cells:
                    check_tags(cell.tags, {"section": section.name, "track": track.name, "bar": bar.index})
            if track.name.lower() in ("chords", "chord"):
                for bar in track.bars:
                    section_index = song.sections.index(section)
                    if resolve_chord_voicing(song, section_index, track, bar) == "guitar" and resolve_voice_leading(song, section_index, track, bar) == "smooth":
                        warn("Smooth voice leading is ignored for guitar chord voicings.", section=section.name, track=track.name, bar=bar.index, rule="guitarVoiceLeadingIgnored", expected="guitar voicing uses deterministic chord shapes")

    num, den = parse_time_signature(song)
    quantize = parse_quantize(song)
    beats_per_bar = num * (4 / den)
    beats_per_bar_int = int(round(beats_per_bar))
    quantize_slots = int(round(beats_per_bar * (quantize / 4)))
    quantize_label = "16th" if quantize == 16 else "8th"
    drum_slots_per_beat = 4 if quantize == 16 else 2
    drum_grid_raw = beats_per_bar * drum_slots_per_beat
    if abs(drum_grid_raw - round(drum_grid_raw)) > 1e-9:
        warn("Non-integer drum grid for quantize; using 16th fallback.", section=None, track="Drums", rule="drumGridCalc", expected="integer grid slots per bar")
        drum_slots_per_beat = 4
        drum_grid_raw = beats_per_bar * drum_slots_per_beat
    drum_grid_slots = int(round(drum_grid_raw))

    for section in song.sections:
        lyrics_track = section.tracks.get("Lyrics")
        melody_track = section.tracks.get("Melody")
        style_value = find_last_tag_value(section.tags, "style") or find_last_tag_value(song.tags, "style")
        drum_pattern_value = find_last_tag_value(section.tags, "drum pattern") or find_last_tag_value(song.tags, "drum pattern")
        if (style_value or drum_pattern_value) and (num, den) != (4, 4):
            warn("Style/template drum generation only supports 4/4 in MVP.", section=section.name, track=None, rule="styleTimeSignature", expected="4/4 for style-driven drums")
        if lyrics_track is not None and melody_track is None:
            warn("Lyrics track has no Melody track to align with.", section=section.name, track="Lyrics", rule="lyricsNoMelody", expected="Melody track in same section")
        if lyrics_track is not None:
            for bar in lyrics_track.bars:
                if len(bar.cells) not in (beats_per_bar_int, quantize_slots):
                    warn("Unexpected lyrics grid length.", section=section.name, track="Lyrics", bar=bar.index, rule="lyricsGrid", expected=f"{beats_per_bar_int} or {quantize_slots} cells ({num}/{den}, {quantize_label})")

    for section_index, section in enumerate(song.sections):
        for track in section.tracks.values():
            for bar in track.bars:
                if track.name.lower() == "melody" and len(bar.cells) not in (beats_per_bar_int, quantize_slots):
                    warn("Unexpected melody grid length.", section=section.name, track=track.name, bar=bar.index, rule="melodyGrid", expected=f"{beats_per_bar_int} or {quantize_slots} cells ({num}/{den}, {quantize_label})")
                if track.name.lower() == "bass" and len(bar.cells) not in (beats_per_bar_int, quantize_slots):
                    warn("Unexpected bass grid length.", section=section.name, track=track.name, bar=bar.index, rule="bassGrid", expected=f"{beats_per_bar_int} or {quantize_slots} cells ({num}/{den}, {quantize_label})")
                if track.name.lower() == "drums" and len(bar.cells) not in (beats_per_bar_int, drum_grid_slots):
                    warn("Unexpected drum grid length.", section=section.name, track=track.name, bar=bar.index, rule="drumGrid", expected="beat-grid or quantize-grid")

                beat_counter = 1
                drum_cells_len = len(bar.cells) if track.name.lower() == "drums" else 0
                drum_is_quantize_grid = drum_cells_len == drum_grid_slots
                for cell in bar.cells:
                    if track.name.lower() == "drums" and drum_is_quantize_grid and len(cell.tokens) > 1:
                        for extra_token in cell.tokens[1:]:
                            err("Too many tokens in drum quantize-grid cell.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=extra_token, rule="drumToken", expected="single token K S H O C T .")
                    for token_index, token in enumerate(cell.tokens):
                        if ALT_ENDING_TOKEN_RE.match(token):
                            continue
                        if track.name.lower() == "melody" and token.startswith("[") and token.endswith("]"):
                            inner_tokens = token[1:-1].strip().split() if token[1:-1].strip() else []
                            expected_len = 4 if quantize == 16 else 2
                            if len(inner_tokens) > expected_len:
                                warn("Melody bracket has too many subdivisions for quantize.", section=section.name, track=track.name, bar=bar.index, token=token, rule="melodyBracketLen", expected=f"1..{expected_len} tokens per beat ({quantize_label})")
                            for inner_token in inner_tokens:
                                if not melody_token_is_note_or_rest(inner_token):
                                    warn("Invalid bracket group token.", section=section.name, track=track.name, bar=bar.index, token=token, rule="melodyBracket", expected="NOTE like G3 or rest R/(R)")
                                    break
                            continue
                        if track.name.lower() == "drums":
                            if token_is_bracket_group(token):
                                inner_tokens = parse_bracket_group(token)
                                if drum_is_quantize_grid:
                                    warn("Bracket group in drum quantize-grid cell is ignored.", section=section.name, track=track.name, bar=bar.index, token=token, rule="drumBracketInGrid", expected="single tokens per grid cell")
                                    continue
                                if len(inner_tokens) != drum_slots_per_beat:
                                    err("Invalid drum bracket length.", section=section.name, track=track.name, bar=bar.index, token=token, rule="drumBracketLen", expected=f"exactly {drum_slots_per_beat} tokens")
                                for inner_token in inner_tokens:
                                    if not drum_token_valid(inner_token):
                                        err("Invalid drum token.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=inner_token, rule="drumToken", expected="K S H O C T .")
                                continue
                            if not drum_token_valid(token):
                                err("Invalid drum token.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=token, rule="drumToken", expected="K S H O C T .")
                            continue
                        if token in ("%", "R", "(R)"):
                            beat_counter += 1
                            continue
                        if track.name.lower() == "melody":
                            if token_index > 0 and cell.tokens[token_index - 1] == ">>":
                                continue
                            if token == ">>":
                                continue
                            parsed, _ = parse_melody_event_tokens(cell.tokens, token_index, 90)
                            if parsed is None:
                                parsed = parse_melody_note_expression(token, 90)
                            if parsed is not None:
                                if parsed.invalid_bend:
                                    warn("Invalid bend amount; bend ignored.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=token, rule="melodyBendNumber", expected="b or bN where N is an integer")
                                if parsed.invalid_vibrato:
                                    warn("Invalid vibrato depth; vibrato ignored.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=token, rule="melodyVibratoNumber", expected="v or vN where N is an integer")
                                if parsed.ramp_target_note is not None:
                                    delta = parsed.ramp_target_note - parsed.midi_note
                                    bend_range = resolve_pitch_bend_range(song, SimpleNamespace(section_index=section_index), track)
                                    if abs(delta) > bend_range:
                                        warn("Pitch ramp exceeds configured bend range; bend will be clamped.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=parsed.source_token, rule="pitchBendRange", expected=f"delta within +/-{bend_range} semitones")
                                continue
                            text = token
                            if text.startswith("~"):
                                text = text[1:]
                                if text == "":
                                    continue
                            if extract_velocity(text) is not None:
                                text = re.sub(r"\([^\)]*\)$", "", text)
                            text = re.sub(r"[-^]+$", "", text)
                            if "/" in text and NOTE_RE.match(text.split("/")[0]) and NOTE_RE.match(text.split("/")[1]):
                                pass
                            else:
                                if text.startswith("(") and text.endswith(")"):
                                    text = text[1:-1]
                                if note_to_midi(text) is None:
                                    err("Invalid melody token.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=token, rule="melodyToken", expected="NOTE like G3 or R")
                        elif track.name.lower() == "bass":
                            if token_index > 0 and cell.tokens[token_index - 1] == ">>":
                                continue
                            if token == ">>":
                                continue
                            parsed, _ = parse_melody_event_tokens(cell.tokens, token_index, 90)
                            if parsed is None:
                                parsed = parse_melody_note_expression(token, 90)
                            if parsed is not None:
                                if parsed.bend_semitones is not None or parsed.vibrato_depth is not None or parsed.ramp_target_note is not None:
                                    warn("Bass expression syntax is ignored in MVP.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=parsed.source_token, rule="bassExprIgnored", expected="plain NOTE, sustain dashes, rest, or bracket group")
                                continue
                            text = token
                            if text.startswith("~"):
                                text = text[1:]
                                if text == "":
                                    continue
                            if extract_velocity(text) is not None:
                                text = re.sub(r"\([^\)]*\)$", "", text)
                            text = re.sub(r"[-^]+$", "", text)
                            if text.startswith("(") and text.endswith(")"):
                                text = text[1:-1]
                            if note_to_midi(text) is None:
                                err("Invalid bass token.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=token, rule="bassToken", expected="NOTE like A2 or rest R")
                        else:
                            normalized = re.sub(r"\([^\)]*\)$", "", token)
                            normalized, string_set, string_error = parse_string_set_suffix(normalized)
                            if string_error is not None:
                                err("Malformed guitar string-set syntax.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=token, rule="guitarStrings", expected="descending unique strings like {6-4-3-2}")
                                beat_counter += 1
                                continue
                            if normalized not in ("", "%", "R", "(R)"):
                                if track.name.lower() in ("chords", "chord"):
                                    spec = parse_chord_symbol(normalized)
                                    if spec is None:
                                        warn("Unrecognized chord symbol.", section=section.name, track=track.name, bar=bar.index, token=token, rule="chordParse", expected="recognized chord symbol")
                                    else:
                                        use_guitar = string_set is not None or resolve_chord_voicing(song, section_index, track, bar) == "guitar"
                                        if use_guitar:
                                            voicing = generate_guitar_voicing_details(
                                                spec,
                                                resolve_guitar_tuning(song, section_index, track, bar),
                                                resolve_capo(song, section_index, track, bar),
                                                string_set=string_set,
                                                position_pref=resolve_guitar_position(song, section_index, track, bar),
                                                low=resolve_chord_range(song, section_index, track, bar)[0],
                                                high=resolve_chord_range(song, section_index, track, bar)[1],
                                            )
                                            if voicing.approx:
                                                warn("Guitar voicing is an approximation for the requested chord or strings.", section=section.name, track=track.name, bar=bar.index, beat=beat_counter, token=token, rule="guitarVoicingApprox", expected="playable exact chord-tone coverage on requested strings")
                                elif track.name.lower() == "bass":
                                    if parse_chord_symbol(normalized) is None:
                                        warn("Unrecognized chord symbol.", section=section.name, track=track.name, bar=bar.index, token=token, rule="chordParse", expected="recognized chord symbol")
                                elif not CHORD_RE.match(normalized):
                                    warn("Unrecognized chord token (accepted in MVP).", section=section.name, track=track.name, bar=bar.index, token=token, rule="chordToken", expected="Chord like Am7, Bb7alt, C/E")
                        beat_counter += 1

    _, struct_issues = build_playback_plan(song)
    for issue in struct_issues:
        if issue.level == "ERROR":
            err(issue.message, section=None, track=None, token=issue.token, rule=issue.rule, expected=issue.expected)
        else:
            warn(issue.message, section=None, track=None, token=issue.token, rule=issue.rule, expected=issue.expected)

    spans_sorted = sorted(collect_melody_spans(song), key=lambda item: (item["start_tick"], item["end_tick"]))
    active: List[Dict[str, Any]] = []
    for span in spans_sorted:
        active = [item for item in active if item["end_tick"] > span["start_tick"]]
        if span["has_bend"]:
            for _ in active:
                warn("Overlapping melody notes share one bend channel.", section=span["section"], track="Melody", bar=span["bar"], rule="melodyPolyBend", expected="monophonic melody while bends or ramps are active")
                break
        elif any(item["has_bend"] for item in active):
            warn("Overlapping melody notes share one bend channel.", section=span["section"], track="Melody", bar=span["bar"], rule="melodyPolyBend", expected="monophonic melody while bends or ramps are active")
        active.append(span)

    for section_report in build_lyrics_alignment_report(song):
        for bar_report in section_report.bars:
            if bar_report.orphan_extenders > 0:
                warn("Lyrics extender appears without a prior lyric.", section=bar_report.section_name, track="Lyrics", bar=bar_report.bar_index, rule="lyricsOrphanExtender", expected="prior lyric token before _")
            if bar_report.overflow_count > 0:
                warn("Lyrics tokens exceed melody note events in bar.", section=bar_report.section_name, track="Lyrics", bar=bar_report.bar_index, rule="lyricsOverflow", expected=f"<= {bar_report.melody_event_count} lyric-bearing tokens; got {bar_report.lyric_token_count}")

    abs_cursor = 0
    bass_spans: List[Dict[str, Any]] = []
    ticks_per_beat = 480
    bar_tick_count = int(round(beats_per_bar * ticks_per_beat))
    grid_tick_count = grid_unit_ticks(ticks_per_beat, quantize)
    for section_index, section in enumerate(song.sections):
        max_bars = max((len(track.bars) for track in section.tracks.values()), default=0)
        bass_track = section.tracks.get("Bass")
        explicit_bass_events = 0
        if bass_track is not None:
            for bar_offset, bar in enumerate(bass_track.bars):
                events, _ = collect_timed_bass_events_for_bar(
                    bar.cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_tick_count,
                    grid_tick_count,
                    abs_cursor + (bar_offset * bar_tick_count),
                    section_name=section.name,
                    section_instance=1,
                    bar_index=bar.index,
                )
                explicit_bass_events += len(events)
                bass_spans.extend(
                    {"section": section.name, "bar": bar.index, "start_tick": event.start, "end_tick": event.start + event.duration}
                    for event in events
                )
        pattern = resolve_bass_pattern(song, SimpleNamespace(section_index=section_index), bass_track)
        style_value = find_last_tag_value(section.tags, "style") or find_last_tag_value(song.tags, "style")
        if pattern is not None and explicit_bass_events == 0 and (section.tracks.get("Chords") is None and section.tracks.get("Chord") is None):
            warn("Bass Pattern requires a Chords track when no explicit Bass notes are written.", section=section.name, track="Bass", rule="bassPatternNoChords", expected="Chords track in same section")
        if style_value and explicit_bass_events == 0 and pattern is not None and (section.tracks.get("Chords") is None and section.tracks.get("Chord") is None):
            warn("Template expansion requires Chords track for generated bass.", section=section.name, track="Bass", rule="templateNoChords", expected="Chords track in same section")
        abs_cursor += max_bars * bar_tick_count

    active_bass: List[Dict[str, Any]] = []
    for span in sorted(bass_spans, key=lambda item: (item["start_tick"], item["end_tick"])):
        active_bass = [item for item in active_bass if item["end_tick"] > span["start_tick"]]
        if active_bass:
            warn("Overlapping bass notes break monophonic bass assumption.", section=span["section"], track="Bass", bar=span["bar"], rule="bassPoly", expected="monophonic bass line")
            continue
        active_bass.append(span)
    return issues
