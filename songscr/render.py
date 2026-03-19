from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import hashlib
import random
import re
from types import SimpleNamespace

from .core import (
    _extract_velocity,
    _collect_timed_bass_events_for_bar,
    _grid_unit_ticks,
    _note_to_midi,
    _parse_quantize,
    _parse_tempo,
    _parse_time_signature,
    _strip_paren_dyn,
    _tokens_for_take,
    build_lyrics_alignment_report,
    has_explicit_pitch_bend_range,
    lint_song,
    parse_song,
    resolve_bass_octave,
    resolve_bass_pattern,
    resolve_bass_rhythm,
    resolve_capo,
    resolve_chord_range,
    resolve_chord_voicing,
    resolve_guitar_position,
    resolve_guitar_tuning,
    resolve_pitch_bend_range,
)
from .automation import extract_auto_ramps, percent_to_cc_value
from .bass import BassEvent, generate_bass_events_from_chords
from .chords import chord_to_midi_notes, parse_chord_symbol
from .guitar_voicings import generate_guitar_voicing, parse_string_set_suffix
from .render_drums import render_drums_bar
from .render_melody import render_melody_bar
from .styles import expand_song_templates
from .struct import build_playback_plan
from .midi import MidiEvent, build_midi, build_track, cc, meta_tempo, meta_time_signature, note_off, note_on, program_change

_ALT_ENDING_RE = re.compile(r'^\{(\d+)\}$')


def _append_pitch_bend_range_rpn(
    melody_events: List[MidiEvent],
    last_ticks: Dict[str, int],
    append_abs,
    abs_tick: int,
    range_semitones: int,
) -> None:
    for controller, value in ((101, 0), (100, 0), (6, range_semitones), (38, 0), (101, 127), (100, 127)):
        append_abs(melody_events, abs_tick, last_ticks, "melody", cc(0, 1, controller, value))




def _cells_for_take(cells, take_number: int):
    return [SimpleNamespace(tokens=_tokens_for_take(cell.tokens, take_number)) for cell in cells]


def _interp_percent(start_percent: int, end_percent: int, start_tick: int, end_tick: int, tick: int) -> int:
    if end_tick <= start_tick:
        return end_percent
    ratio = (tick - start_tick) / (end_tick - start_tick)
    ratio = max(0.0, min(1.0, ratio))
    return int(round(start_percent + ((end_percent - start_percent) * ratio)))


def render_midi_bytes(text: str, seed: Optional[int]=None, strict: bool=False) -> bytes:
    song = expand_song_templates(parse_song(text))
    issues = lint_song(text, strict=strict)
    errors = [i for i in issues if i.level == "ERROR"]
    if errors:
        msgs = "\n".join(e.format_line() for e in errors)
        raise ValueError(f"Lint failed:\n{msgs}")
    playback_plan, struct_issues = build_playback_plan(song)
    struct_errors = [i for i in struct_issues if i.level == "ERROR"]
    if struct_errors:
        msgs = "\n".join(f'ERROR rule="{e.rule}" {e.message}' for e in struct_errors)
        raise ValueError(f"Struct planning failed:\n{msgs}")

    num, den = _parse_time_signature(song)
    bpm = _parse_tempo(song)
    quantize = _parse_quantize(song)

    ppq = 480
    ticks_per_beat = ppq
    beats_per_bar = num * (4 / den)
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))
    grid_unit_ticks = _grid_unit_ticks(ticks_per_beat, quantize)

    # deterministic seed
    if seed is None:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        seed = int(h[:8], 16)
    rnd = random.Random(seed)
    _ = rnd

    # Track 0 meta
    meta_events = [
        meta_time_signature(0, num, den),
        meta_tempo(0, bpm),
    ]
    meta_track = build_track(meta_events)

    music_tracks: List[bytes] = []

    chord_events: List[MidiEvent] = [program_change(0, 0, 0)]
    melody_events: List[MidiEvent] = [program_change(0, 1, 80)]
    bass_events: List[MidiEvent] = [program_change(0, 2, 32)]
    drum_events: List[MidiEvent] = []  # channel 9 is drums, no program change

    def append_abs(events: List[MidiEvent], abs_ticks: int, last_tick_holder: Dict[str, int], key: str, data_ev: MidiEvent):
        last = last_tick_holder.get(key, 0)
        delta = abs_ticks - last
        if delta < 0:
            delta = 0
        events.append(MidiEvent(delta, data_ev.data))
        last_tick_holder[key] = abs_ticks

    last_ticks = {"chords": 0, "melody": 0, "bass": 0, "drums": 0}

    drum_map = {"K":36,"S":38,"H":42,"O":46,"C":49,"T":47}
    track_events = {
        "chords": chord_events,
        "melody": melody_events,
        "bass": bass_events,
        "drums": drum_events,
    }
    track_to_channel = {"chords": 0, "chord": 0, "melody": 1, "drums": 9, "bass": 2}
    channel_to_track_key = {0: "chords", 1: "melody", 2: "bass", 9: "drums"}

    timeline = []
    abs_probe = 0
    for sec_inst in playback_plan:
        sec = song.sections[sec_inst.section_index]
        tracks = sec.tracks
        max_bars = 0
        for tr in tracks.values():
            max_bars = max(max_bars, len(tr.bars))
        timeline.append(
            {
                "section_index": sec_inst.section_index,
                "section": sec,
                "take_number": sec_inst.take_number,
                "start_tick": abs_probe,
                "max_bars": max_bars,
            }
        )
        abs_probe += max_bars * bar_ticks
    song_end_tick = abs_probe

    auto_candidates = []
    ramps = extract_auto_ramps(song)
    lyric_schedule: Dict[int, List[str]] = {}
    for section_report in build_lyrics_alignment_report(song):
        for bar_report in section_report.bars:
            for attachment in bar_report.attachments:
                if attachment.text:
                    lyric_schedule.setdefault(attachment.note_start, []).append(attachment.text)

    def add_scope_candidates(
        start_tick: int,
        end_tick: int,
        bar_count: int,
        channels: List[int],
        cc_num: int,
        start_percent: int,
        end_percent: int,
        priority: int,
        order: int,
    ) -> None:
        if not channels:
            return
        ticks = [start_tick + (i * bar_ticks) for i in range(bar_count + 1)]
        if end_tick not in ticks:
            ticks.append(end_tick)
        for tick in ticks:
            pct = _interp_percent(start_percent, end_percent, start_tick, end_tick, tick)
            val = percent_to_cc_value(pct)
            for ch in channels:
                auto_candidates.append((tick, ch, cc_num, val, priority, order))

    global_channels = sorted(
        {
            track_to_channel.get(name.lower())
            for item in timeline
            for name in item["section"].tracks.keys()
            if track_to_channel.get(name.lower()) is not None
        }
    )

    for ramp in ramps:
        if ramp.cc is None:
            continue
        if ramp.scope == "global":
            add_scope_candidates(
                start_tick=0,
                end_tick=song_end_tick,
                bar_count=int(round(song_end_tick / bar_ticks)),
                channels=global_channels,
                cc_num=ramp.cc,
                start_percent=ramp.start_percent,
                end_percent=ramp.end_percent,
                priority=0,
                order=ramp.order,
            )
            continue
        if ramp.scope == "section":
            for item in timeline:
                if item["section_index"] != ramp.section_index:
                    continue
                chans = sorted(
                    {
                        track_to_channel.get(name.lower())
                        for name in item["section"].tracks.keys()
                        if track_to_channel.get(name.lower()) is not None
                    }
                )
                add_scope_candidates(
                    start_tick=item["start_tick"],
                    end_tick=item["start_tick"] + (item["max_bars"] * bar_ticks),
                    bar_count=item["max_bars"],
                    channels=chans,
                    cc_num=ramp.cc,
                    start_percent=ramp.start_percent,
                    end_percent=ramp.end_percent,
                    priority=1,
                    order=ramp.order,
                )
            continue
        if ramp.scope == "track":
            if ramp.track_name is None:
                continue
            ch = track_to_channel.get(ramp.track_name.lower())
            if ch is None:
                continue
            for item in timeline:
                if item["section_index"] != ramp.section_index:
                    continue
                tr = None
                for key, val in item["section"].tracks.items():
                    if key.lower() == ramp.track_name.lower():
                        tr = val
                        break
                if tr is None:
                    continue
                bar_count = len(tr.bars)
                add_scope_candidates(
                    start_tick=item["start_tick"],
                    end_tick=item["start_tick"] + (bar_count * bar_ticks),
                    bar_count=bar_count,
                    channels=[ch],
                    cc_num=ramp.cc,
                    start_percent=ramp.start_percent,
                    end_percent=ramp.end_percent,
                    priority=2,
                    order=ramp.order,
                )

    winner: Dict[Tuple[int, int, int], Tuple[Tuple[int, int], int]] = {}
    for cand in auto_candidates:
        tick, ch, cc_num, val, priority, order = cand
        key = (tick, ch, cc_num)
        prev = winner.get(key)
        rank = (priority, order)
        if prev is None or rank >= prev[0]:
            winner[key] = (rank, val)

    automation_schedule: Dict[str, Dict[int, List[Tuple[int, int]]]] = {
        "chords": {},
        "melody": {},
        "bass": {},
        "drums": {},
    }
    for (tick, ch, cc_num), (_, val) in sorted(winner.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        track_key = channel_to_track_key.get(ch)
        if track_key is None:
            continue
        automation_schedule.setdefault(track_key, {}).setdefault(tick, []).append((cc_num, val))

    def flush_automation(track_key: str, abs_tick: int) -> None:
        track_schedule = automation_schedule.get(track_key)
        if not track_schedule:
            return
        events = track_events[track_key]
        for cc_num, value in sorted(track_schedule.pop(abs_tick, []), key=lambda item: item[0]):
            append_abs(events, abs_tick, last_ticks, track_key, cc(0, track_to_channel[track_key], cc_num, value))

    abs_cursor = 0
    should_emit_bend_range_rpn = has_explicit_pitch_bend_range(song)
    current_melody_bend_range: Optional[int] = None
    for sec_inst in playback_plan:
        sec = song.sections[sec_inst.section_index]
        take_number = sec_inst.take_number
        tracks = sec.tracks
        trm = tracks.get("Melody")
        trb = tracks.get("Bass")
        resolved_melody_bend_range = resolve_pitch_bend_range(song, sec_inst, trm)
        explicit_bass_by_bar: Dict[int, List] = {}
        explicit_bass_present = False
        if trb is not None:
            for bar_index, bar in enumerate(trb.bars):
                filtered_cells = _cells_for_take(bar.cells, take_number)
                bass_bar_events, _ = _collect_timed_bass_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_unit_ticks,
                    abs_cursor + (bar_index * bar_ticks),
                    section_name=sec.name,
                    section_instance=sec_inst.instance_number,
                    bar_index=bar.index,
                )
                explicit_bass_by_bar[bar_index] = bass_bar_events
                if bass_bar_events:
                    explicit_bass_present = True
        generated_bass_by_bar: Dict[int, List[BassEvent]] = {}
        if not explicit_bass_present:
            bass_pattern = resolve_bass_pattern(song, sec_inst, trb)
            chords_track = tracks.get("Chords") or tracks.get("Chord")
            if bass_pattern is not None and chords_track is not None:
                generated_events = generate_bass_events_from_chords(
                    sec,
                    bass_pattern,
                    resolve_bass_rhythm(song, sec_inst, trb),
                    resolve_bass_octave(song, sec_inst, trb),
                    {
                        "take_number": take_number,
                        "abs_cursor": abs_cursor,
                        "bar_duration": bar_ticks,
                        "beat_duration": ticks_per_beat,
                        "beats_per_bar": int(round(beats_per_bar)),
                        "max_bars": max(len(track.bars) for track in tracks.values()) if tracks else 0,
                    },
                )
                for event in generated_events:
                    bar_index = int((event.start - abs_cursor) // bar_ticks) if bar_ticks > 0 else 0
                    generated_bass_by_bar.setdefault(bar_index, []).append(event)
        if should_emit_bend_range_rpn and current_melody_bend_range != resolved_melody_bend_range:
            _append_pitch_bend_range_rpn(
                melody_events=melody_events,
                last_ticks=last_ticks,
                append_abs=append_abs,
                abs_tick=abs_cursor,
                range_semitones=resolved_melody_bend_range,
            )
            current_melody_bend_range = resolved_melody_bend_range
        max_bars = 0
        for tr in tracks.values():
            max_bars = max(max_bars, len(tr.bars))
        for bi in range(max_bars):
            flush_automation("chords", abs_cursor)
            flush_automation("melody", abs_cursor)
            flush_automation("bass", abs_cursor)
            flush_automation("drums", abs_cursor)

            tr = tracks.get("Chords") or tracks.get("Chord") or None
            if tr and bi < len(tr.bars):
                bar = tr.bars[bi]
                cells = _cells_for_take(bar.cells, take_number)
                beat = 0
                for cell in cells:
                    if not cell.tokens:
                        beat += 1
                        continue
                    tok = cell.tokens[0]
                    if tok in ("%", "R", "(R)"):
                        beat += 1
                        continue
                    vel = _extract_velocity(tok) or 75
                    t = _strip_paren_dyn(tok)
                    t, string_set, _ = parse_string_set_suffix(t)
                    spec = parse_chord_symbol(t)
                    start = abs_cursor + beat * ticks_per_beat
                    dur = ticks_per_beat
                    if spec:
                        use_guitar = string_set is not None or resolve_chord_voicing(song, sec_inst.section_index, tr, bar) == "guitar"
                        if use_guitar:
                            tuning = resolve_guitar_tuning(song, sec_inst.section_index, tr, bar)
                            capo = resolve_capo(song, sec_inst.section_index, tr, bar)
                            position = resolve_guitar_position(song, sec_inst.section_index, tr, bar)
                            low, high = resolve_chord_range(song, sec_inst.section_index, tr, bar)
                            notes = generate_guitar_voicing(
                                spec,
                                tuning,
                                capo,
                                string_set=string_set,
                                position_pref=position,
                                low=low,
                                high=high,
                            )
                        else:
                            notes = chord_to_midi_notes(spec, root_octave=3)
                        for n in notes:
                            append_abs(chord_events, start, last_ticks, "chords", note_on(0, 0, n, vel))
                        for n in notes:
                            append_abs(chord_events, start + dur, last_ticks, "chords", note_off(0, 0, n, 0))
                    else:
                        root = re.match(r'^([A-G](?:#|b)?)', t)
                        if root:
                            n = _note_to_midi(root.group(1) + "3")
                            if n is not None:
                                append_abs(chord_events, start, last_ticks, "chords", note_on(0, 0, n, vel))
                                append_abs(chord_events, start + dur, last_ticks, "chords", note_off(0, 0, n, 0))
                    beat += 1

            if trm and bi < len(trm.bars):
                bar = trm.bars[bi]
                cells = _cells_for_take(bar.cells, take_number)
                render_melody_bar(
                    melody_events=melody_events,
                    last_ticks=last_ticks,
                    append_abs=append_abs,
                    abs_cursor=abs_cursor,
                    bar_cells=cells,
                    beats_per_bar=beats_per_bar,
                    quantize=quantize,
                    ticks_per_beat=ticks_per_beat,
                    bar_ticks=bar_ticks,
                    grid_unit_ticks=grid_unit_ticks,
                    bend_range_semitones=resolved_melody_bend_range,
                    lyric_schedule=lyric_schedule,
                )

            bass_bar_events = explicit_bass_by_bar.get(bi, [])
            if not bass_bar_events:
                bass_bar_events = generated_bass_by_bar.get(bi, [])
            for bass_event in sorted(bass_bar_events, key=lambda item: (item.start, item.midi_note)):
                append_abs(bass_events, bass_event.start, last_ticks, "bass", note_on(0, 2, bass_event.midi_note, bass_event.velocity))
                append_abs(bass_events, bass_event.start + bass_event.duration, last_ticks, "bass", note_off(0, 2, bass_event.midi_note, 0))

            trd = tracks.get("Drums")
            if trd and bi < len(trd.bars):
                bar = trd.bars[bi]
                cells = _cells_for_take(bar.cells, take_number)
                render_drums_bar(
                    drum_events=drum_events,
                    last_ticks=last_ticks,
                    abs_cursor=abs_cursor,
                    bar_cells=cells,
                    beats_per_bar=beats_per_bar,
                    quantize=quantize,
                    bar_ticks=bar_ticks,
                    drum_map=drum_map,
                    append_abs=append_abs,
                )

            abs_cursor += bar_ticks

    flush_automation("chords", song_end_tick)
    flush_automation("melody", song_end_tick)
    flush_automation("bass", song_end_tick)
    flush_automation("drums", song_end_tick)

    if chord_events and len(chord_events) > 1:
        music_tracks.append(build_track(chord_events))
    if melody_events and len(melody_events) > 1:
        music_tracks.append(build_track(melody_events))
    if bass_events and len(bass_events) > 1:
        music_tracks.append(build_track(bass_events))
    if drum_events:
        music_tracks.append(build_track(drum_events))

    return build_midi([meta_track] + music_tracks, ppq=ppq)
