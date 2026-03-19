
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any
import re
from types import SimpleNamespace

from .ast import Song, Section, Track, Bar, Cell, Tag
from .automation import auto_param_to_cc, parse_auto
from .bass import generate_bass_events_from_chords
from .chords import parse_chord_symbol
from .guitar_voicings import STANDARD_GUITAR_TUNING, generate_guitar_voicing_details, parse_guitar_tuning, parse_string_set_suffix
from .struct import build_playback_plan, parse_struct_line

TRACK_ALIASES = {
    "Chord Track:": "Chords",
    "Melody Track:": "Melody",
    "Lyrics Track:": "Lyrics",
    "Bass Track:": "Bass",
    "Drums Track:": "Drums",
}

NOTE_RE = re.compile(r'^[A-G](?:#|b)?[0-9]$')
CHORD_RE = re.compile(r'^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus)?[0-9]*(?:add[0-9]+)?(?:[#b][0-9]+)?(?:alt)?(?:/[A-G](?:#|b)?)?$')
TAG_RE = re.compile(r'^\[(?P<name>[^\]:]+)(?::\s*(?P<value>[^\]]+))?\]$')
SECTION_RE = re.compile(r'^\[(?P<name>[A-Za-z0-9_\- ]+)\]\s*$')
TRACK_RE = re.compile(r'^\[Track:\s*(?P<name>[^\]]+)\]\s*$')
ALT_ENDING_TOKEN_RE = re.compile(r'^\{\d+\}$')

KNOWN_TAGS = {
    "mode","key","tempo","time signature","quantize","feel","swing","humanize","velocity rand","pocket","capo","scale","auto",
    "reverb","chorus","delay","compression","eq","track vol","mute","solo","chord voicing","melody instrument","drums","bass instrument",
    "pitch bend range","bass pattern","bass octave","bass rhythm","style","drum pattern","template mode",
    "voicing","guitar tuning","guitar position","chord range","voice leading"
}

@dataclass
class LintIssue:
    level: str   # ERROR or WARN
    message: str
    section: Optional[str] = None
    track: Optional[str] = None
    bar: Optional[int] = None
    beat: Optional[int] = None
    token: Optional[str] = None
    rule: Optional[str] = None
    expected: Optional[str] = None

    def format_line(self, file: Optional[str]=None) -> str:
        parts = [self.level]
        if file:
            parts.append(f'file={file}')
        if self.section is not None:
            parts.append(f'section={self.section}')
        if self.track is not None:
            parts.append(f'track={self.track}')
        if self.bar is not None:
            parts.append(f'bar={self.bar}')
        if self.beat is not None:
            parts.append(f'beat={self.beat}')
        if self.token is not None:
            parts.append(f'token="{self.token}"')
        if self.rule is not None:
            parts.append(f'rule="{self.rule}"')
        if self.expected is not None:
            parts.append(f'expected="{self.expected}"')
        parts.append(self.message)
        return " ".join(parts)

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

def parse_tags_from_line(line: str, scope: str) -> List[Tag]:
    tags = []
    # allow multiple tags on a line: [Key: A] [Tempo: 90]
    for m in re.finditer(r'\[[^\]]+\]', line):
        raw = m.group(0).strip()
        tm = TAG_RE.match(raw)
        if tm:
            name = tm.group('name').strip()
            val = tm.group('value')
            tags.append(Tag(name=name, value=val.strip() if val else None, scope=scope))
    return tags

def _split_bar_cells(line: str) -> Optional[List[str]]:
    # expects something like: | a | b | c |
    if '|' not in line:
        return None
    stripped = line.strip()
    if not stripped.startswith('|') or stripped.count('|') < 2:
        return None
    # split, dropping first and last empty
    parts = [p.strip() for p in stripped.split('|')]
    # parts[0] and parts[-1] should be empty if ends with |
    if parts and parts[0] == '':
        parts = parts[1:]
    if parts and parts[-1] == '':
        parts = parts[:-1]
    return parts


def _tokens_for_take(tokens: List[str], take_number: int) -> List[str]:
    out: List[str] = []
    active_take: Optional[int] = None
    for tok in tokens:
        m = ALT_ENDING_TOKEN_RE.match(tok)
        if m:
            active_take = int(tok[1:-1])
            continue
        if active_take is None or active_take == take_number:
            out.append(tok)
    return out

def _extract_bar_tags_and_strip(cell_raw: str) -> Tuple[List[Tag], str]:
    tags: List[Tag] = []
    out: List[str] = []
    idx = 0
    for m in re.finditer(r'\[[^\]]+\]', cell_raw):
        raw = m.group(0)
        tm = TAG_RE.match(raw)
        is_tag = False
        if tm:
            name = tm.group('name').strip()
            val = tm.group('value')
            if ':' in raw or name.lower() in KNOWN_TAGS:
                is_tag = True
                tags.append(Tag(name=name, value=val.strip() if val else None, scope="bar"))
        if is_tag:
            out.append(cell_raw[idx:m.start()])
        else:
            out.append(cell_raw[idx:m.start()])
            out.append(raw)
        idx = m.end()
    out.append(cell_raw[idx:])
    return tags, "".join(out).strip()

def _tokenize_cell(text: str) -> List[str]:
    tokens: List[str] = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == '[':
            j = text.find(']', i + 1)
            if j == -1:
                tokens.append(text[i:].strip())
                break
            tokens.append(text[i:j + 1].strip())
            i = j + 1
            continue
        j = i
        while j < len(text) and not text[j].isspace():
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens

def parse_song(text: str) -> Song:
    song = Song(meta={}, tags=[], sections=[], struct=[], struct_items=[])
    current_section: Optional[Section] = None
    current_track: Optional[Track] = None
    current_track_name: Optional[str] = None

    lines = text.splitlines()
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped == "":
            continue

        # Struct lines
        if stripped.startswith("#") or stripped.startswith("[Goto:") or stripped.startswith("[Repeat:") or stripped.startswith("[Fade:"):
            song.struct.append(stripped)
            parsed_struct = parse_struct_line(stripped)
            if parsed_struct is not None:
                parsed_struct["section_index"] = len(song.sections) - 1 if song.sections else None
                song.struct_items.append(parsed_struct)
            continue

        # Section header
        sm = SECTION_RE.match(stripped)
        if sm:
            name = sm.group("name").strip()
            current_section = Section(name=name, tags=[], tracks={})
            song.sections.append(current_section)
            current_track = None
            current_track_name = None
            continue

        # Track header (two styles)
        if stripped in TRACK_ALIASES:
            if current_section is None:
                # create implicit section
                current_section = Section(name="Main", tags=[], tracks={})
                song.sections.append(current_section)
            tname = TRACK_ALIASES[stripped]
            current_track_name = tname
            current_track = current_section.tracks.get(tname) or Track(name=tname, tags=[], bars=[])
            current_section.tracks[tname] = current_track
            continue

        tm = TRACK_RE.match(stripped)
        if tm:
            if current_section is None:
                current_section = Section(name="Main", tags=[], tracks={})
                song.sections.append(current_section)
            tname = tm.group("name").strip()
            current_track_name = tname
            current_track = current_section.tracks.get(tname) or Track(name=tname, tags=[], bars=[])
            current_section.tracks[tname] = current_track
            continue

        # Tags line
        if stripped.startswith('[') and ']' in stripped and stripped.count('[') >= 1 and stripped.count(']') >= 1:
            tags = parse_tags_from_line(stripped, scope="global" if current_section is None else "section" if current_track is None else "track")
            if tags:
                if current_section is None:
                    song.tags.extend(tags)
                    # also populate meta from common tags
                    for t in tags:
                        key = t.name.lower().strip()
                        if key in ("key", "tempo", "time signature", "quantize", "feel", "swing", "pocket", "capo", "scale", "humanize", "velocity rand", "pitch bend range", "bass pattern", "bass octave", "bass rhythm", "voicing", "guitar tuning", "guitar position", "chord range", "voice leading"):
                            song.meta[key] = t.value if t.value is not None else True
                else:
                    if current_track is None:
                        current_section.tags.extend(tags)
                    else:
                        current_track.tags.extend(tags)
                continue

        # Bar row
        cells = _split_bar_cells(stripped)
        if cells is not None:
            if current_section is None:
                current_section = Section(name="Main", tags=[], tracks={})
                song.sections.append(current_section)
            if current_track is None:
                # infer default track if none selected
                current_track = current_section.tracks.get("Chords") or Track(name="Chords", tags=[], bars=[])
                current_section.tracks["Chords"] = current_track
                current_track_name = "Chords"
            bar_index = len(current_track.bars) + 1
            bar = Bar(index=bar_index, cells=[])
            for cell_raw in cells:
                cell = Cell(raw=cell_raw, tokens=[], tags=[])
                # extract inline tags, but keep bracket groups
                inline_tags, without_tags = _extract_bar_tags_and_strip(cell_raw)
                cell.tags.extend(inline_tags)
                # tokens split on whitespace, preserving bracket groups
                cell.tokens = _tokenize_cell(without_tags)
                bar.cells.append(cell)
            current_track.bars.append(bar)
            continue

        # fallback: ignore (could be comments)
        # If you want comment syntax later, add it here.

    return song

def emit_song(song: Song) -> str:
    def fmt_tag(t: Tag) -> str:
        if t.value is None:
            return f'[{t.name}]'
        return f'[{t.name}: {t.value}]'

    # Canonical global tag ordering (partial; unknown tags keep input order)
    ORDER = ["Mode","Style","Template Mode","Key","Tempo","Time Signature","Quantize","Feel","Swing","Humanize","Velocity Rand","Pocket","Capo","Scale","Voicing","Voice Leading","Guitar Tuning","Guitar Position","Chord Range","Pitch Bend Range","Drum Pattern","Bass Pattern","Bass Octave","Bass Rhythm"]
    def tag_sort_key(t: Tag) -> Tuple[int, str]:
        try:
            return (ORDER.index(t.name), "")
        except ValueError:
            return (999, t.name.lower())

    out: List[str] = []
    if song.tags:
        tags_sorted = sorted(song.tags, key=tag_sort_key)
        out.append(" ".join(fmt_tag(t) for t in tags_sorted))

    for sec in song.sections:
        out.append("")
        out.append(f'[{sec.name}]')
        if sec.tags:
            tags_sorted = sorted(sec.tags, key=tag_sort_key)
            out.append(" ".join(fmt_tag(t) for t in tags_sorted))
        # stable track order
        track_order = ["Chords","Melody","Lyrics","Bass","Drums","Automation"]
        for tname in sorted(sec.tracks.keys(), key=lambda x: (track_order.index(x) if x in track_order else 99, x.lower())):
            tr = sec.tracks[tname]
            out.append("")
            out.append(f'[Track: {tr.name}]')
            if tr.tags:
                tags_sorted = sorted(tr.tags, key=tag_sort_key)
                out.append(" ".join(fmt_tag(t) for t in tags_sorted))
            # bars
            for bar in tr.bars:
                parts = []
                for cell in bar.cells:
                    # normalize pitch ramp spacing and ghost notes
                    cell_text = " ".join(cell.tokens)
                    cell_text = re.sub(r'\s*>>\s*', ' >> ', cell_text)
                    # ghost note normalization: lowercase note token -> (NOTE) not implemented here; parser doesn't detect lowercase
                    # inline tags appended at end in canonical order
                    if cell.tags:
                        cell_text = (cell_text + " " if cell_text else "") + " ".join(fmt_tag(t) for t in sorted(cell.tags, key=tag_sort_key))
                    parts.append(cell_text.strip())
                out.append("| " + " | ".join(parts) + " |")
        # struct lines related? (kept in song.struct globally)

    if song.struct:
        out.append("")
        out.extend(song.struct)

    # trim leading/trailing blank lines
    return "\n".join(out).strip() + "\n"

def format_song(text: str) -> str:
    """Canonical formatter. Parses then re-emits a stable layout."""
    song = parse_song(text)
    formatted = emit_song(song)
    return formatted

def _note_to_midi(note: str) -> Optional[int]:
    m = NOTE_RE.match(note)
    if not m:
        return None
    pc = note[0]
    rest = note[1:]
    accidental = ""
    octave = None
    if rest and rest[0] in "#b":
        accidental = rest[0]
        octave = int(rest[1:])
    else:
        octave = int(rest)
    base = {"C":0,"D":2,"E":4,"F":5,"G":7,"A":9,"B":11}[pc]
    if accidental == "#":
        base += 1
    elif accidental == "b":
        base -= 1
    midi = 12 * (octave + 1) + base
    return midi if 0 <= midi <= 127 else None

def _parse_quantize(song: Song) -> int:
    q = song.meta.get("quantize")
    if q is None:
        return 16
    s = str(q).strip().lower()
    m = re.search(r'(\d+)', s)
    if not m:
        return 16
    val = int(m.group(1))
    return 8 if val == 8 else 16

def _grid_unit_ticks(ticks_per_beat: int, quantize: int) -> int:
    return int(round(ticks_per_beat * (4 / quantize)))

def _melody_token_is_note_or_rest(tok: str) -> bool:
    if tok in ("R", "(R)"):
        return True
    t = tok
    if t.startswith("~"):
        t = t[1:]
        if t == "":
            return True
    if _extract_velocity(t) is not None:
        t = re.sub(r'\([^\)]*\)$', '', t)
    t = re.sub(r'[-^]+$', '', t)
    t = re.sub(r'(v\d+|b\d+|v|b)$', '', t)
    if "/" in t and NOTE_RE.match(t.split("/")[0]) and NOTE_RE.match(t.split("/")[1]):
        return True
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1]
    return _note_to_midi(t) is not None


def _parse_pitch_bend_range_value(raw_value: Optional[str]) -> Optional[int]:
    if raw_value is None:
        return None
    try:
        return int(str(raw_value).strip())
    except ValueError:
        return None


def _find_last_tag_value(tags: List[Tag], tag_name: str) -> Optional[str]:
    target = tag_name.strip().lower()
    for tag in reversed(tags):
        if tag.name.strip().lower() == target:
            return tag.value
    return None


def _bar_tags(bar: Optional[Bar]) -> List[Tag]:
    if bar is None:
        return []
    return [tag for cell in bar.cells for tag in cell.tags]


def resolve_scoped_tag_value(song: Song, section_index: Optional[int], track: Optional[Track], bar: Optional[Bar], tag_name: str) -> Optional[str]:
    resolved = _find_last_tag_value(song.tags, tag_name)
    if isinstance(section_index, int) and 0 <= section_index < len(song.sections):
        section_value = _find_last_tag_value(song.sections[section_index].tags, tag_name)
        if section_value is not None:
            resolved = section_value
    if track is not None:
        track_value = _find_last_tag_value(track.tags, tag_name)
        if track_value is not None:
            resolved = track_value
    bar_value = _find_last_tag_value(_bar_tags(bar), tag_name)
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
    match = re.match(r'^\s*([A-G](?:#|b)?-?\d+)\s*\.\.\s*([A-G](?:#|b)?-?\d+)\s*$', str(value))
    if match is None:
        return None, None
    low = _note_to_midi(match.group(1))
    high = _note_to_midi(match.group(2))
    if low is None or high is None:
        return None, None
    if low > high:
        low, high = high, low
    return low, high


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

    global_value = _find_last_tag_value(song.tags, "pitch bend range")
    parsed_global = _parse_pitch_bend_range_value(global_value)
    if parsed_global is not None and 1 <= parsed_global <= 24:
        resolved = parsed_global

    section_index = getattr(section_instance, "section_index", None)
    if isinstance(section_index, int) and 0 <= section_index < len(song.sections):
        section_value = _find_last_tag_value(song.sections[section_index].tags, "pitch bend range")
        parsed_section = _parse_pitch_bend_range_value(section_value)
        if parsed_section is not None and 1 <= parsed_section <= 24:
            resolved = parsed_section

    if track is not None:
        track_value = _find_last_tag_value(track.tags, "pitch bend range")
        parsed_track = _parse_pitch_bend_range_value(track_value)
        if parsed_track is not None and 1 <= parsed_track <= 24:
            resolved = parsed_track

    return resolved


def resolve_bass_pattern(song: Song, section_instance, track: Optional[Track]) -> Optional[str]:
    resolved: Optional[str] = None

    global_value = _find_last_tag_value(song.tags, "bass pattern")
    if global_value:
        resolved = str(global_value).strip().lower()

    section_index = getattr(section_instance, "section_index", None)
    if isinstance(section_index, int) and 0 <= section_index < len(song.sections):
        section_value = _find_last_tag_value(song.sections[section_index].tags, "bass pattern")
        if section_value:
            resolved = str(section_value).strip().lower()

    if track is not None:
        track_value = _find_last_tag_value(track.tags, "bass pattern")
        if track_value:
            resolved = str(track_value).strip().lower()

    if resolved in ("root", "root5", "octave", "walkup", "pedal"):
        return resolved
    return None


def resolve_bass_octave(song: Song, section_instance, track: Optional[Track]) -> int:
    resolved = 2
    for tag_list in (
        song.tags,
        song.sections[getattr(section_instance, "section_index", -1)].tags if isinstance(getattr(section_instance, "section_index", None), int) and 0 <= getattr(section_instance, "section_index", -1) < len(song.sections) else [],
        track.tags if track is not None else [],
    ):
        value = _find_last_tag_value(tag_list, "bass octave")
        if value is None:
            continue
        try:
            resolved = int(str(value).strip())
        except ValueError:
            continue
    return max(0, min(5, resolved))


def resolve_bass_rhythm(song: Song, section_instance, track: Optional[Track]) -> str:
    resolved = "quarters"
    for tag_list in (
        song.tags,
        song.sections[getattr(section_instance, "section_index", -1)].tags if isinstance(getattr(section_instance, "section_index", None), int) and 0 <= getattr(section_instance, "section_index", -1) < len(song.sections) else [],
        track.tags if track is not None else [],
    ):
        value = _find_last_tag_value(tag_list, "bass rhythm")
        if value is None:
            continue
        val = str(value).strip().lower()
        if val in ("quarters", "eighths"):
            resolved = val
    return resolved

def lint_song(text: str, filename: Optional[str]=None, strict: bool=False) -> List[LintIssue]:
    song = parse_song(text)
    issues: List[LintIssue] = []

    # helpers
    def warn(msg, **kw):
        issues.append(LintIssue("WARN", msg, **kw))
    def err(msg, **kw):
        issues.append(LintIssue("ERROR", msg, **kw))

    # unknown tags handling
    def check_tags(tag_list: List[Tag], scope_info: Dict[str, Any]):
        for t in tag_list:
            if t.name.lower().strip() not in KNOWN_TAGS:
                (err if strict else warn)(
                    f'Unknown tag [{t.name}] preserved.',
                    **scope_info,
                    token=f'[{t.name}]',
                    rule="unknownTag",
                    expected="known tag or --lenient"
                )
            if t.name.lower().strip() == "auto":
                if t.value is None:
                    err(
                        "Malformed Auto ramp syntax.",
                        **scope_info,
                        token=f"[Auto: {t.value or ''}]",
                        rule="autoSyntax",
                        expected='Auto: Param 0%->100%'
                    )
                    continue
                try:
                    param, _, _ = parse_auto(t.value)
                except ValueError:
                    err(
                        "Malformed Auto ramp syntax.",
                        **scope_info,
                        token=f"[Auto: {t.value or ''}]",
                        rule="autoSyntax",
                        expected='Auto: Param 0%->100%'
                    )
                    continue
                if auto_param_to_cc(param) is None:
                    warn(
                        "Unknown Auto parameter.",
                        **scope_info,
                        token=f"[Auto: {t.value}]",
                        rule="autoParamUnknown",
                        expected="reverb chorus filter_cutoff cutoff filter expression volume pan"
                    )
            if t.name.lower().strip() == "pitch bend range":
                parsed_range = _parse_pitch_bend_range_value(t.value)
                if parsed_range is None or not 1 <= parsed_range <= 24:
                    err(
                        "Pitch bend range must be an integer within 1..24 semitones.",
                        **scope_info,
                        token=f"[Pitch Bend Range: {t.value or ''}]",
                        rule="bendRange",
                        expected="integer semitones within 1..24"
                    )
            if t.name.lower().strip() == "style":
                style_value = str(t.value or "").strip().lower()
                if style_value and style_value not in ("slowblues", "straightrock", "funklite"):
                    warn(
                        "Unknown Style value.",
                        **scope_info,
                        token=f"[Style: {t.value or ''}]",
                        rule="styleUnknown",
                        expected="SlowBlues StraightRock FunkLite"
                    )
            if t.name.lower().strip() == "drum pattern":
                drum_value = str(t.value or "").strip().lower()
                if drum_value and drum_value not in ("halftimeshuffle", "straight8rock", "fouronfloor", "funk16kick", "balladsparse"):
                    warn(
                        "Unknown Drum Pattern value.",
                        **scope_info,
                        token=f"[Drum Pattern: {t.value or ''}]",
                        rule="drumPatternUnknown",
                        expected="HalfTimeShuffle Straight8Rock FourOnFloor Funk16Kick BalladSparse"
                    )
            if t.name.lower().strip() == "bass pattern":
                bass_value = str(t.value or "").strip().lower()
                if bass_value and bass_value not in ("root", "root5", "octave", "walkup", "pedal"):
                    warn(
                        "Unknown Bass Pattern value.",
                        **scope_info,
                        token=f"[Bass Pattern: {t.value or ''}]",
                        rule="bassPatternUnknown",
                        expected="Root Root5 Octave WalkUp Pedal"
                    )
            if t.name.lower().strip() == "guitar tuning":
                if t.value is None:
                    warn(
                        "Invalid Guitar Tuning value; using standard tuning.",
                        **scope_info,
                        token="[Guitar Tuning]",
                        rule="guitarTuning",
                        expected="six note names like E2 A2 D3 G3 B3 E4"
                    )
                    continue
                try:
                    parse_guitar_tuning(t.value)
                except ValueError:
                    warn(
                        "Invalid Guitar Tuning value; using standard tuning.",
                        **scope_info,
                        token=f"[Guitar Tuning: {t.value}]",
                        rule="guitarTuning",
                        expected="six note names like E2 A2 D3 G3 B3 E4"
                    )

    check_tags(song.tags, {"section": None, "track": None})
    for sec in song.sections:
        check_tags(sec.tags, {"section": sec.name, "track": None})
        for trname, tr in sec.tracks.items():
            check_tags(tr.tags, {"section": sec.name, "track": tr.name})
            for bar in tr.bars:
                for cell in bar.cells:
                    check_tags(cell.tags, {"section": sec.name, "track": tr.name, "bar": bar.index})
            if tr.name.lower() in ("chords", "chord"):
                for bar in tr.bars:
                    if resolve_chord_voicing(song, song.sections.index(sec), tr, bar) == "guitar" and resolve_voice_leading(song, song.sections.index(sec), tr, bar) == "smooth":
                        warn(
                            "Smooth voice leading is ignored for guitar chord voicings.",
                            section=sec.name,
                            track=tr.name,
                            bar=bar.index,
                            rule="guitarVoiceLeadingIgnored",
                            expected="guitar voicing uses deterministic chord shapes"
                        )

    # token checks (minimal)
    num, den = _parse_time_signature(song)
    quantize = _parse_quantize(song)
    beats_per_bar = num * (4 / den)
    beats_per_bar_int = int(round(beats_per_bar))
    quantize_slots = int(round(beats_per_bar * (quantize / 4)))
    quantize_label = "16th" if quantize == 16 else "8th"
    drum_slots_per_beat = 4 if quantize == 16 else 2
    drum_grid_raw = beats_per_bar * drum_slots_per_beat
    if abs(drum_grid_raw - round(drum_grid_raw)) > 1e-9:
        warn(
            "Non-integer drum grid for quantize; using 16th fallback.",
            section=None,
            track="Drums",
            rule="drumGridCalc",
            expected="integer grid slots per bar"
        )
        drum_slots_per_beat = 4
        drum_grid_raw = beats_per_bar * drum_slots_per_beat
    drum_grid_slots = int(round(drum_grid_raw))

    for sec in song.sections:
        lyrics_track = sec.tracks.get("Lyrics")
        melody_track = sec.tracks.get("Melody")
        style_value = _find_last_tag_value(sec.tags, "style") or _find_last_tag_value(song.tags, "style")
        drum_pattern_value = _find_last_tag_value(sec.tags, "drum pattern") or _find_last_tag_value(song.tags, "drum pattern")
        if (style_value or drum_pattern_value) and (num, den) != (4, 4):
            warn(
                "Style/template drum generation only supports 4/4 in MVP.",
                section=sec.name,
                track=None,
                rule="styleTimeSignature",
                expected="4/4 for style-driven drums"
            )
        if lyrics_track is not None and melody_track is None:
            warn(
                "Lyrics track has no Melody track to align with.",
                section=sec.name,
                track="Lyrics",
                rule="lyricsNoMelody",
                expected="Melody track in same section"
            )
        if lyrics_track is not None:
            for bar in lyrics_track.bars:
                cells_len = len(bar.cells)
                if cells_len not in (beats_per_bar_int, quantize_slots):
                    warn(
                        "Unexpected lyrics grid length.",
                        section=sec.name,
                        track="Lyrics",
                        bar=bar.index,
                        rule="lyricsGrid",
                        expected=f"{beats_per_bar_int} or {quantize_slots} cells ({num}/{den}, {quantize_label})"
                    )

    for section_index, sec in enumerate(song.sections):
        for trname, tr in sec.tracks.items():
            for bar in tr.bars:
                if tr.name.lower() == "melody":
                    cells_len = len(bar.cells)
                    if cells_len not in (beats_per_bar_int, quantize_slots):
                        warn(
                            "Unexpected melody grid length.",
                            section=sec.name,
                            track=tr.name,
                            bar=bar.index,
                            rule="melodyGrid",
                            expected=f"{beats_per_bar_int} or {quantize_slots} cells ({num}/{den}, {quantize_label})"
                        )
                if tr.name.lower() == "bass":
                    cells_len = len(bar.cells)
                    if cells_len not in (beats_per_bar_int, quantize_slots):
                        warn(
                            "Unexpected bass grid length.",
                            section=sec.name,
                            track=tr.name,
                            bar=bar.index,
                            rule="bassGrid",
                            expected=f"{beats_per_bar_int} or {quantize_slots} cells ({num}/{den}, {quantize_label})"
                        )
                if tr.name.lower() == "drums":
                    cells_len = len(bar.cells)
                    if cells_len not in (beats_per_bar_int, drum_grid_slots):
                        warn(
                            "Unexpected drum grid length.",
                            section=sec.name,
                            track=tr.name,
                            bar=bar.index,
                            rule="drumGrid",
                            expected="beat-grid or quantize-grid"
                        )
                beat_counter = 1
                drum_cells_len = len(bar.cells) if tr.name.lower() == "drums" else 0
                drum_is_beat_grid = drum_cells_len == beats_per_bar_int
                drum_is_quantize_grid = drum_cells_len == drum_grid_slots
                for cell in bar.cells:
                    if tr.name.lower() == "drums" and drum_is_quantize_grid and len(cell.tokens) > 1:
                        for extra_tok in cell.tokens[1:]:
                            err(
                                "Too many tokens in drum quantize-grid cell.",
                                section=sec.name,
                                track=tr.name,
                                bar=bar.index,
                                beat=beat_counter,
                                token=extra_tok,
                                rule="drumToken",
                                expected="single token K S H O C T ."
                            )
                    for token_index, tok in enumerate(cell.tokens):
                        if ALT_ENDING_TOKEN_RE.match(tok):
                            continue
                        if tr.name.lower() == "melody" and tok.startswith("[") and tok.endswith("]"):
                            inner = tok[1:-1].strip()
                            inner_tokens = inner.split() if inner else []
                            expected_len = 4 if quantize == 16 else 2
                            if len(inner_tokens) > expected_len:
                                warn(
                                    "Melody bracket has too many subdivisions for quantize.",
                                    section=sec.name,
                                    track=tr.name,
                                    bar=bar.index,
                                    token=tok,
                                    rule="melodyBracketLen",
                                    expected=f"1..{expected_len} tokens per beat ({quantize_label})"
                                )
                            for inner_tok in inner_tokens:
                                if not _melody_token_is_note_or_rest(inner_tok):
                                    warn(
                                        "Invalid bracket group token.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        token=tok,
                                        rule="melodyBracket",
                                        expected="NOTE like G3 or rest R/(R)"
                                    )
                                    break
                            continue
                        if tr.name.lower() == "drums":
                            if _token_is_bracket_group(tok):
                                inner_tokens = _parse_bracket_group(tok)
                                if drum_is_quantize_grid:
                                    warn(
                                        "Bracket group in drum quantize-grid cell is ignored.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        token=tok,
                                        rule="drumBracketInGrid",
                                        expected="single tokens per grid cell"
                                    )
                                    continue
                                if len(inner_tokens) != drum_slots_per_beat:
                                    err(
                                        "Invalid drum bracket length.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        token=tok,
                                        rule="drumBracketLen",
                                        expected=f"exactly {drum_slots_per_beat} tokens"
                                    )
                                for inner_tok in inner_tokens:
                                    if not _drum_token_valid(inner_tok):
                                        err(
                                            "Invalid drum token.",
                                            section=sec.name,
                                            track=tr.name,
                                            bar=bar.index,
                                            beat=beat_counter,
                                            token=inner_tok,
                                            rule="drumToken",
                                            expected="K S H O C T ."
                                        )
                                continue
                            if not _drum_token_valid(tok):
                                err("Invalid drum token.", section=sec.name, track=tr.name, bar=bar.index, beat=beat_counter,
                                    token=tok, rule="drumToken", expected="K S H O C T .")
                            continue
                        if tok in ("%", "R", "(R)"):
                            beat_counter += 1
                            continue
                        # dynamics like (mf) or (127) appear attached to chord/note; we validate in parse during render
                        # Melody tokens: NOTE with optional suffixes like --, ^, (mf), v5, b2, /A4, etc.
                        if tr.name.lower() == "melody":
                            if token_index > 0 and cell.tokens[token_index - 1] == ">>":
                                continue
                            if tok == ">>":
                                continue
                            parsed, _ = _parse_melody_event_tokens(cell.tokens, token_index, 90)
                            if parsed is None:
                                parsed = _parse_melody_note_expression(tok, 90)
                            if parsed is not None:
                                if parsed.invalid_bend:
                                    warn(
                                        "Invalid bend amount; bend ignored.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        beat=beat_counter,
                                        token=tok,
                                        rule="melodyBendNumber",
                                        expected="b or bN where N is an integer"
                                    )
                                if parsed.invalid_vibrato:
                                    warn(
                                        "Invalid vibrato depth; vibrato ignored.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        beat=beat_counter,
                                        token=tok,
                                        rule="melodyVibratoNumber",
                                        expected="v or vN where N is an integer"
                                    )
                                if parsed.ramp_target_note is not None:
                                    delta = parsed.ramp_target_note - parsed.midi_note
                                    bend_range = resolve_pitch_bend_range(song, SimpleNamespace(section_index=section_index), tr)
                                    if abs(delta) > bend_range:
                                        warn(
                                            "Pitch ramp exceeds configured bend range; bend will be clamped.",
                                            section=sec.name,
                                            track=tr.name,
                                            bar=bar.index,
                                            beat=beat_counter,
                                            token=parsed.source_token,
                                            rule="pitchBendRange",
                                            expected=f"delta within +/-{bend_range} semitones"
                                        )
                                continue
                            t = tok
                            if t.startswith("~"):
                                t = t[1:]
                                if t == "":
                                    continue
                            if _extract_velocity(t) is not None:
                                t = re.sub(r'\([^\)]*\)$', '', t)
                            t = re.sub(r'[-^]+$', '', t)
                            if "/" in t and NOTE_RE.match(t.split("/")[0]) and NOTE_RE.match(t.split("/")[1]):
                                pass
                            else:
                                if t.startswith("(") and t.endswith(")"):
                                    t = t[1:-1]
                                n = _note_to_midi(t)
                                if n is None:
                                    err("Invalid melody token.", section=sec.name, track=tr.name, bar=bar.index, beat=beat_counter,
                                        token=tok, rule="melodyToken", expected="NOTE like G3 or R")
                        elif tr.name.lower() == "bass":
                            if token_index > 0 and cell.tokens[token_index - 1] == ">>":
                                continue
                            if tok == ">>":
                                continue
                            parsed, _ = _parse_melody_event_tokens(cell.tokens, token_index, 90)
                            if parsed is None:
                                parsed = _parse_melody_note_expression(tok, 90)
                            if parsed is not None:
                                if parsed.bend_semitones is not None or parsed.vibrato_depth is not None or parsed.ramp_target_note is not None:
                                    warn(
                                        "Bass expression syntax is ignored in MVP.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        beat=beat_counter,
                                        token=parsed.source_token,
                                        rule="bassExprIgnored",
                                        expected="plain NOTE, sustain dashes, rest, or bracket group"
                                    )
                                continue
                            t = tok
                            if t.startswith("~"):
                                t = t[1:]
                                if t == "":
                                    continue
                            if _extract_velocity(t) is not None:
                                t = re.sub(r'\([^\)]*\)$', '', t)
                            t = re.sub(r'[-^]+$', '', t)
                            if t.startswith("(") and t.endswith(")"):
                                t = t[1:-1]
                            n = _note_to_midi(t)
                            if n is None:
                                err("Invalid bass token.", section=sec.name, track=tr.name, bar=bar.index, beat=beat_counter,
                                    token=tok, rule="bassToken", expected="NOTE like A2 or rest R")
                        elif tr.name.lower() == "drums":
                            pass
                        else:
                            t = re.sub(r'\([^\)]*\)$', '', tok)  # dynamics
                            t, string_set, string_error = parse_string_set_suffix(t)
                            if string_error is not None:
                                err(
                                    "Malformed guitar string-set syntax.",
                                    section=sec.name,
                                    track=tr.name,
                                    bar=bar.index,
                                    beat=beat_counter,
                                    token=tok,
                                    rule="guitarStrings",
                                    expected="descending unique strings like {6-4-3-2}"
                                )
                                beat_counter += 1
                                continue
                            if t == "" or t in ("%", "R", "(R)"):
                                pass
                            elif tr.name.lower() in ("chords", "chord"):
                                spec = parse_chord_symbol(t)
                                if spec is None:
                                    warn(
                                        "Unrecognized chord symbol.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        token=tok,
                                        rule="chordParse",
                                        expected="recognized chord symbol"
                                    )
                                else:
                                    use_guitar = string_set is not None or resolve_chord_voicing(song, song.sections.index(sec), tr, bar) == "guitar"
                                    if use_guitar:
                                        tuning = resolve_guitar_tuning(song, song.sections.index(sec), tr, bar)
                                        capo = resolve_capo(song, song.sections.index(sec), tr, bar)
                                        position = resolve_guitar_position(song, song.sections.index(sec), tr, bar)
                                        low, high = resolve_chord_range(song, song.sections.index(sec), tr, bar)
                                        voicing = generate_guitar_voicing_details(
                                            spec,
                                            tuning,
                                            capo,
                                            string_set=string_set,
                                            position_pref=position,
                                            low=low,
                                            high=high,
                                        )
                                        if voicing.approx:
                                            warn(
                                                "Guitar voicing is an approximation for the requested chord or strings.",
                                                section=sec.name,
                                                track=tr.name,
                                                bar=bar.index,
                                                beat=beat_counter,
                                                token=tok,
                                                rule="guitarVoicingApprox",
                                                expected="playable exact chord-tone coverage on requested strings"
                                            )
                            elif tr.name.lower() == "bass":
                                if parse_chord_symbol(t) is None:
                                    warn(
                                        "Unrecognized chord symbol.",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        token=tok,
                                        rule="chordParse",
                                        expected="recognized chord symbol"
                                    )
                            else:
                                if not CHORD_RE.match(t):
                                    warn(
                                        "Unrecognized chord token (accepted in MVP).",
                                        section=sec.name,
                                        track=tr.name,
                                        bar=bar.index,
                                        token=tok,
                                        rule="chordToken",
                                        expected="Chord like Am7, Bb7alt, C/E"
                                    )
                        beat_counter += 1

    _, struct_issues = build_playback_plan(song)
    for si in struct_issues:
        if si.level == "ERROR":
            err(
                si.message,
                section=None,
                track=None,
                token=si.token,
                rule=si.rule,
                expected=si.expected,
            )
        else:
            warn(
                si.message,
                section=None,
                track=None,
                token=si.token,
                rule=si.rule,
                expected=si.expected,
            )

    spans = _collect_melody_spans(song)
    spans_sorted = sorted(spans, key=lambda item: (item["start_tick"], item["end_tick"]))
    active: List[Dict[str, Any]] = []
    for span in spans_sorted:
        active = [item for item in active if item["end_tick"] > span["start_tick"]]
        if span["has_bend"]:
            for other in active:
                warn(
                    "Overlapping melody notes share one bend channel.",
                    section=span["section"],
                    track="Melody",
                    bar=span["bar"],
                    rule="melodyPolyBend",
                    expected="monophonic melody while bends or ramps are active"
                )
                break
        elif any(item["has_bend"] for item in active):
            warn(
                "Overlapping melody notes share one bend channel.",
                section=span["section"],
                track="Melody",
                bar=span["bar"],
                rule="melodyPolyBend",
                expected="monophonic melody while bends or ramps are active"
            )
        active.append(span)

    for section_report in build_lyrics_alignment_report(song):
        for bar_report in section_report.bars:
            if bar_report.orphan_extenders > 0:
                warn(
                    "Lyrics extender appears without a prior lyric.",
                    section=bar_report.section_name,
                    track="Lyrics",
                    bar=bar_report.bar_index,
                    rule="lyricsOrphanExtender",
                    expected="prior lyric token before _"
                )
            if bar_report.overflow_count > 0:
                warn(
                    "Lyrics tokens exceed melody note events in bar.",
                    section=bar_report.section_name,
                    track="Lyrics",
                    bar=bar_report.bar_index,
                    rule="lyricsOverflow",
                    expected=f"<= {bar_report.melody_event_count} lyric-bearing tokens; got {bar_report.lyric_token_count}"
                )

    abs_cursor = 0
    bass_spans: List[Dict[str, Any]] = []
    ppq = 480
    ticks_per_beat = ppq
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))
    grid_unit_ticks = _grid_unit_ticks(ticks_per_beat, quantize)
    for section_index, sec in enumerate(song.sections):
        tracks = sec.tracks
        max_bars = 0
        for track in tracks.values():
            max_bars = max(max_bars, len(track.bars))
        bass_track = tracks.get("Bass")
        explicit_bass_events = 0
        if bass_track is not None:
            for bar_offset, bar in enumerate(bass_track.bars):
                events, ignored_tokens = _collect_timed_bass_events_for_bar(
                    bar.cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_unit_ticks,
                    abs_cursor + (bar_offset * bar_ticks),
                    section_name=sec.name,
                    section_instance=1,
                    bar_index=bar.index,
                )
                explicit_bass_events += len(events)
                for event in events:
                    bass_spans.append(
                        {
                            "section": sec.name,
                            "bar": bar.index,
                            "start_tick": event.start,
                            "end_tick": event.start + event.duration,
                        }
                    )
        pattern = resolve_bass_pattern(song, SimpleNamespace(section_index=section_index), bass_track)
        style_value = _find_last_tag_value(sec.tags, "style") or _find_last_tag_value(song.tags, "style")
        if pattern is not None and explicit_bass_events == 0 and (tracks.get("Chords") is None and tracks.get("Chord") is None):
            warn(
                "Bass Pattern requires a Chords track when no explicit Bass notes are written.",
                section=sec.name,
                track="Bass",
                rule="bassPatternNoChords",
                expected="Chords track in same section"
            )
        if style_value and explicit_bass_events == 0 and pattern is not None and (tracks.get("Chords") is None and tracks.get("Chord") is None):
            warn(
                "Template expansion requires Chords track for generated bass.",
                section=sec.name,
                track="Bass",
                rule="templateNoChords",
                expected="Chords track in same section"
            )
        abs_cursor += max_bars * bar_ticks

    bass_spans.sort(key=lambda item: (item["start_tick"], item["end_tick"]))
    active_bass: List[Dict[str, Any]] = []
    for span in bass_spans:
        active_bass = [item for item in active_bass if item["end_tick"] > span["start_tick"]]
        if active_bass:
            warn(
                "Overlapping bass notes break monophonic bass assumption.",
                section=span["section"],
                track="Bass",
                bar=span["bar"],
                rule="bassPoly",
                expected="monophonic bass line"
            )
            continue
        active_bass.append(span)

    return issues

def _parse_time_signature(song: Song) -> Tuple[int, int]:
    ts = song.meta.get("time signature")
    if isinstance(ts, str) and "/" in ts:
        a, b = ts.split("/", 1)
        try:
            return int(a.strip()), int(b.strip())
        except ValueError:
            pass
    return 4, 4

def _parse_tempo(song: Song) -> int:
    tempo = song.meta.get("tempo")
    if tempo is None:
        return 120
    try:
        return int(str(tempo).strip())
    except ValueError:
        return 120

def _dyn_to_vel(d: str) -> Optional[int]:
    m = d.strip().lower()
    table = {"pp":30,"p":45,"mp":60,"mf":75,"f":95,"ff":115}
    return table.get(m)

def _extract_velocity(token: str) -> Optional[int]:
    m = re.search(r'\(([^)]+)\)$', token)
    if not m:
        return None
    v = m.group(1).strip()
    if v.isdigit():
        return max(0, min(127, int(v)))
    return _dyn_to_vel(v)

def _strip_paren_dyn(token: str) -> str:
    return re.sub(r'\([^)]+\)$', '', token)

def _strip_dyn_if_present(token: str) -> str:
    return _strip_paren_dyn(token) if _extract_velocity(token) is not None else token

def _parse_melody_note_expression(tok: str, default_vel: int) -> Optional[MelodyEventSpec]:
    if tok in ("R", "(R)", "%", ">>"):
        return None
    t = tok
    vel = _extract_velocity(t) or default_vel
    t = _strip_dyn_if_present(t)
    if t.startswith("~"):
        t = t[1:]
        if t == "":
            return None
    sustain_match = re.search(r'(-+)$', t)
    dashes = len(sustain_match.group(1)) if sustain_match else 0
    t = re.sub(r'(-+)$', '', t)
    t = t.rstrip("^")
    ghost = False
    if t.startswith("(") and t.endswith(")"):
        ghost = True
        t = t[1:-1]

    if "/" in t:
        t = t.split("/", 1)[0]

    match = re.match(r'^(?P<note>[A-G](?:#|b)?[0-9])(?:(?P<kind>[bv])(?P<amount>.*))?$', t)
    if match is None:
        return None
    note_text = match.group("note")
    kind = match.group("kind")
    amount_text = match.group("amount")

    midi_note = _note_to_midi(note_text)
    if midi_note is None:
        return None

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
        source_token=tok,
        midi_note=midi_note,
        velocity=vel,
        dashes=dashes,
        ghost=ghost,
        bend_semitones=bend_semitones,
        vibrato_depth=vibrato_depth,
        invalid_bend=invalid_bend,
        invalid_vibrato=invalid_vibrato,
    )

def _parse_melody_note_token(tok: str, default_vel: int) -> Optional[Tuple[int, int, int, bool]]:
    parsed = _parse_melody_note_expression(tok, default_vel)
    if parsed is None:
        return None
    return parsed.midi_note, parsed.velocity, parsed.dashes, parsed.ghost

def _parse_melody_event_tokens(tokens: List[str], index: int, default_vel: int) -> Tuple[Optional[MelodyEventSpec], int]:
    if index >= len(tokens):
        return None, 1
    token = tokens[index]
    if ">>" in token and token != ">>":
        left, right = token.split(">>", 1)
        start = _parse_melody_note_expression(left.strip(), default_vel)
        end = _parse_melody_note_expression(right.strip(), default_vel)
        if start is None or end is None:
            return None, 1
        end_dashes = end.dashes if end.dashes > 0 else start.dashes
        return MelodyEventSpec(
            source_token=token,
            midi_note=start.midi_note,
            velocity=end.velocity if right.strip() else start.velocity,
            dashes=end_dashes,
            ghost=start.ghost,
            bend_semitones=None,
            vibrato_depth=None,
            ramp_target_note=end.midi_note,
            invalid_bend=start.invalid_bend or end.invalid_bend,
            invalid_vibrato=start.invalid_vibrato or end.invalid_vibrato,
        ), 1
    if index + 2 < len(tokens) and tokens[index + 1] == ">>":
        start = _parse_melody_note_expression(tokens[index], default_vel)
        end = _parse_melody_note_expression(tokens[index + 2], default_vel)
        if start is None or end is None:
            return None, 1
        end_dashes = end.dashes if end.dashes > 0 else start.dashes
        return MelodyEventSpec(
            source_token=f"{tokens[index]} >> {tokens[index + 2]}",
            midi_note=start.midi_note,
            velocity=end.velocity,
            dashes=end_dashes,
            ghost=start.ghost,
            bend_semitones=None,
            vibrato_depth=None,
            ramp_target_note=end.midi_note,
            invalid_bend=start.invalid_bend or end.invalid_bend,
            invalid_vibrato=start.invalid_vibrato or end.invalid_vibrato,
        ), 3
    parsed = _parse_melody_note_expression(token, default_vel)
    return parsed, 1

def _token_is_bracket_group(tok: str) -> bool:
    return tok.startswith("[") and tok.endswith("]")

def _parse_bracket_group(tok: str) -> List[str]:
    inner = tok[1:-1].strip()
    return inner.split() if inner else []

def _drum_token_valid(tok: str) -> bool:
    return tok in ("K","S","H","O","C","T",".")

def _melody_grid_slots_per_bar(beats_per_bar: float, quantize: int) -> int:
    return int(round(beats_per_bar * (quantize / 4)))

def _melody_grid_mode(cell_count: int, beats_per_bar_int: int, grid_slots_per_bar: int) -> str:
    if cell_count == beats_per_bar_int:
        return "beat"
    if cell_count == grid_slots_per_bar and cell_count > 0:
        return "quantize"
    return "other"

def _melody_cell_step_ticks(
    mode: str,
    ticks_per_beat: int,
    grid_unit_ticks: int,
    bar_ticks: int,
    cell_count: int,
) -> float:
    if mode == "beat":
        return float(ticks_per_beat)
    if mode == "quantize":
        return float(grid_unit_ticks)
    if cell_count <= 0:
        return float(bar_ticks)
    return bar_ticks / cell_count


def _lyrics_token_estimated_syllables(token: str) -> int:
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


def _lyric_token_payload(token: str) -> Tuple[str, bool]:
    phrase_boundary = token.endswith("/")
    text = token[:-1] if phrase_boundary else token
    return text, phrase_boundary


def _collect_timed_melody_events_for_bar(
    bar_cells,
    beats_per_bar: float,
    quantize: int,
    ticks_per_beat: int,
    bar_ticks: int,
    grid_unit_ticks: int,
    bar_start_tick: int,
    *,
    section_name: Optional[str] = None,
    section_instance: Optional[int] = None,
    bar_index: Optional[int] = None,
) -> List[TimedMelodyEvent]:
    events: List[TimedMelodyEvent] = []
    cell_count = len(bar_cells)
    beats_per_bar_int = int(round(beats_per_bar))
    grid_slots_per_bar = _melody_grid_slots_per_bar(beats_per_bar, quantize)
    mode = _melody_grid_mode(cell_count, beats_per_bar_int, grid_slots_per_bar)
    cell_step_ticks = _melody_cell_step_ticks(mode, ticks_per_beat, grid_unit_ticks, bar_ticks, cell_count)

    def add_inner(inner_tokens: List[str], inner_start_base: int, inner_ticks: float) -> None:
        idx = 0
        while idx < len(inner_tokens):
            spec, consumed = _parse_melody_event_tokens(inner_tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                start_tick = inner_start_base + int(round(idx * inner_ticks))
                slot_ticks = max(1, int(round(inner_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_ticks)
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
        cell_start = bar_start_tick + int(round(cell_index * cell_step_ticks))

        if mode == "beat":
            bracket_tokens = [tok for tok in tokens if _token_is_bracket_group(tok)]
            if bracket_tokens:
                for token in bracket_tokens:
                    inner_tokens = _parse_bracket_group(token)
                    if inner_tokens:
                        inner_ticks = ticks_per_beat / len(inner_tokens)
                        add_inner(inner_tokens, cell_start, inner_ticks)
                continue

        sub_count = max(1, len(tokens))
        sub_ticks = cell_step_ticks / sub_count
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            slot_start = bar_start_tick + int(round((cell_index * cell_step_ticks) + (idx * sub_ticks)))
            if _token_is_bracket_group(token):
                inner_tokens = _parse_bracket_group(token)
                if inner_tokens:
                    inner_ticks = sub_ticks / len(inner_tokens)
                    add_inner(inner_tokens, slot_start, inner_ticks)
                idx += 1
                continue
            spec, consumed = _parse_melody_event_tokens(tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                slot_ticks = max(1, int(round(sub_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_ticks)
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


def _collect_timed_bass_events_for_bar(
    bar_cells,
    beats_per_bar: float,
    quantize: int,
    ticks_per_beat: int,
    bar_ticks: int,
    grid_unit_ticks: int,
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
    grid_slots_per_bar = _melody_grid_slots_per_bar(beats_per_bar, quantize)
    mode = _melody_grid_mode(cell_count, beats_per_bar_int, grid_slots_per_bar)
    cell_step_ticks = _melody_cell_step_ticks(mode, ticks_per_beat, grid_unit_ticks, bar_ticks, cell_count)

    def add_inner(inner_tokens: List[str], inner_start_base: int, inner_ticks: float) -> None:
        idx = 0
        while idx < len(inner_tokens):
            spec, consumed = _parse_melody_event_tokens(inner_tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                if spec.bend_semitones is not None or spec.vibrato_depth is not None or spec.ramp_target_note is not None:
                    ignored_expr_tokens.append(spec.source_token)
                start_tick = inner_start_base + int(round(idx * inner_ticks))
                slot_ticks = max(1, int(round(inner_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_ticks)
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
        cell_start = bar_start_tick + int(round(cell_index * cell_step_ticks))

        if mode == "beat":
            bracket_tokens = [tok for tok in tokens if _token_is_bracket_group(tok)]
            if bracket_tokens:
                for token in bracket_tokens:
                    inner_tokens = _parse_bracket_group(token)
                    if inner_tokens:
                        inner_ticks = ticks_per_beat / len(inner_tokens)
                        add_inner(inner_tokens, cell_start, inner_ticks)
                continue

        sub_count = max(1, len(tokens))
        sub_ticks = cell_step_ticks / sub_count
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            slot_start = bar_start_tick + int(round((cell_index * cell_step_ticks) + (idx * sub_ticks)))
            if _token_is_bracket_group(token):
                inner_tokens = _parse_bracket_group(token)
                if inner_tokens:
                    inner_ticks = sub_ticks / len(inner_tokens)
                    add_inner(inner_tokens, slot_start, inner_ticks)
                idx += 1
                continue
            spec, consumed = _parse_melody_event_tokens(tokens, idx, 90)
            consumed = max(1, consumed)
            if spec is not None:
                if spec.bend_semitones is not None or spec.vibrato_depth is not None or spec.ramp_target_note is not None:
                    ignored_expr_tokens.append(spec.source_token)
                slot_ticks = max(1, int(round(sub_ticks)))
                duration_ticks = slot_ticks + (spec.dashes * grid_unit_ticks)
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


def _collect_lyric_tokens_for_bar(bar_cells) -> List[str]:
    return [token for cell in bar_cells for token in cell.tokens]


def build_lyrics_alignment_report(song: Song) -> List[LyricsSectionReport]:
    num, den = _parse_time_signature(song)
    quantize = _parse_quantize(song)
    ppq = 480
    ticks_per_beat = ppq
    beats_per_bar = num * (4 / den)
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))
    grid_unit_ticks = _grid_unit_ticks(ticks_per_beat, quantize)
    playback_plan, _ = build_playback_plan(song)

    reports: List[LyricsSectionReport] = []
    abs_cursor = 0
    for sec_inst in playback_plan:
        section = song.sections[sec_inst.section_index]
        tracks = section.tracks
        max_bars = 0
        for track in tracks.values():
            max_bars = max(max_bars, len(track.bars))

        melody_track = tracks.get("Melody")
        lyrics_track = tracks.get("Lyrics")
        bar_reports: List[LyricsBarAlignment] = []
        section_melody_count = 0
        section_lyric_tokens = 0
        section_overflow = 0
        section_orphans = 0
        section_syllables = 0
        last_emitted_lyric: Optional[str] = None

        for bar_offset in range(max_bars):
            bar_index = bar_offset + 1
            bar_start_tick = abs_cursor + (bar_offset * bar_ticks)
            melody_cells = []
            lyrics_cells = []
            if melody_track is not None and bar_offset < len(melody_track.bars):
                melody_cells = melody_track.bars[bar_offset].cells
            if lyrics_track is not None and bar_offset < len(lyrics_track.bars):
                lyrics_cells = lyrics_track.bars[bar_offset].cells

            melody_events = _collect_timed_melody_events_for_bar(
                melody_cells,
                beats_per_bar,
                quantize,
                ticks_per_beat,
                bar_ticks,
                grid_unit_ticks,
                bar_start_tick,
                section_name=section.name,
                section_instance=sec_inst.instance_number,
                bar_index=bar_index,
            )
            lyric_tokens = _collect_lyric_tokens_for_bar(lyrics_cells)
            estimated_syllables = sum(_lyrics_token_estimated_syllables(tok) for tok in lyric_tokens)

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
                                text=None,
                                extend=True,
                                phrase_boundary=False,
                            )
                        )
                        melody_pointer += 1
                    else:
                        overflow_count += 1
                    continue
                if melody_pointer >= len(melody_events):
                    overflow_count += 1
                    continue
                lyric_text, phrase_boundary = _lyric_token_payload(token)
                attachments.append(
                    AlignedLyricEvent(
                        note_index=melody_pointer,
                        note_start=melody_events[melody_pointer].start,
                        text=lyric_text,
                        extend=False,
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
                    section_instance=sec_inst.instance_number,
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
                section_instance=sec_inst.instance_number,
                melody_event_count=section_melody_count,
                lyric_token_count=section_lyric_tokens,
                overflow_count=section_overflow,
                orphan_extenders=section_orphans,
                estimated_syllables=section_syllables,
                bars=bar_reports,
            )
        )
        abs_cursor += max_bars * bar_ticks

    return reports

def _collect_melody_spans(song: Song) -> List[Dict[str, Any]]:
    num, den = _parse_time_signature(song)
    quantize = _parse_quantize(song)
    ppq = 480
    ticks_per_beat = ppq
    beats_per_bar = num * (4 / den)
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))
    grid_unit_ticks = _grid_unit_ticks(ticks_per_beat, quantize)
    spans: List[Dict[str, Any]] = []

    abs_cursor = 0
    for sec in song.sections:
        tracks = sec.tracks
        max_bars = 0
        for tr in tracks.values():
            max_bars = max(max_bars, len(tr.bars))
        trm = tracks.get("Melody")
        if trm is not None:
            for bi in range(len(trm.bars)):
                bar = trm.bars[bi]
                bar_start = abs_cursor + (bi * bar_ticks)
                cell_count = len(bar.cells)
                beats_per_bar_int = int(round(beats_per_bar))
                grid_slots_per_bar = _melody_grid_slots_per_bar(beats_per_bar, quantize)
                mode = _melody_grid_mode(cell_count, beats_per_bar_int, grid_slots_per_bar)
                cell_step_ticks = _melody_cell_step_ticks(mode, ticks_per_beat, grid_unit_ticks, bar_ticks, cell_count)

                for ci, cell in enumerate(bar.cells):
                    tokens = cell.tokens
                    if not tokens:
                        continue
                    cell_start = bar_start + int(round(ci * cell_step_ticks))

                    def add_inner(inner_tokens: List[str], inner_start_base: int, inner_ticks: float) -> None:
                        idx = 0
                        while idx < len(inner_tokens):
                            spec, consumed = _parse_melody_event_tokens(inner_tokens, idx, 90)
                            consumed = max(1, consumed)
                            if spec is not None:
                                start_tick = inner_start_base + int(round(idx * inner_ticks))
                                slot_ticks = max(1, int(round(inner_ticks)))
                                dur = slot_ticks + spec.dashes * grid_unit_ticks
                                spans.append(
                                    {
                                        "section": sec.name,
                                        "track": "Melody",
                                        "bar": bar.index,
                                        "start_tick": start_tick,
                                        "end_tick": start_tick + dur,
                                        "has_bend": spec.bend_semitones is not None or spec.ramp_target_note is not None,
                                    }
                                )
                            idx += consumed

                    if mode == "beat":
                        bracket_tokens = [tok for tok in tokens if _token_is_bracket_group(tok)]
                        if bracket_tokens:
                            for tok in bracket_tokens:
                                inner_tokens = _parse_bracket_group(tok)
                                if not inner_tokens:
                                    continue
                                inner_ticks = ticks_per_beat / len(inner_tokens)
                                add_inner(inner_tokens, cell_start, inner_ticks)
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
                                add_inner(inner_tokens, slot_start, inner_ticks)
                            idx += 1
                            continue
                        spec, consumed = _parse_melody_event_tokens(tokens, idx, 90)
                        consumed = max(1, consumed)
                        if spec is not None:
                            dur = slot_ticks + spec.dashes * grid_unit_ticks
                            spans.append(
                                {
                                    "section": sec.name,
                                    "track": "Melody",
                                    "bar": bar.index,
                                    "start_tick": slot_start,
                                    "end_tick": slot_start + dur,
                                    "has_bend": spec.bend_semitones is not None or spec.ramp_target_note is not None,
                                }
                            )
                        idx += consumed

        abs_cursor += max_bars * bar_ticks

    return spans

def song_stats(text: str) -> Dict[str, Any]:
    from .styles import expand_song_templates

    source_song = parse_song(text)
    song = expand_song_templates(parse_song(text))
    sec_count = len(song.sections)
    track_names = set()
    bars_total = 0
    notes = 0
    drum_hits = 0
    num, den = _parse_time_signature(song)
    quantize = _parse_quantize(song)
    beats_per_bar = num * (4 / den)
    ppq = 480
    ticks_per_beat = ppq
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))
    grid_unit_ticks = _grid_unit_ticks(ticks_per_beat, quantize)
    for sec in song.sections:
        for tr in sec.tracks.values():
            track_names.add(tr.name)
            bars_total += len(tr.bars)
            for bar in tr.bars:
                for cell in bar.cells:
                    for tok in cell.tokens:
                        if tr.name.lower() == "melody":
                            t = _strip_paren_dyn(tok)
                            t = re.sub(r'[-^]+$', '', t)
                            t = re.sub(r'(v\d+|b\d+|v|b)$', '', t)
                            if t in ("R","(R)","%",">>"):
                                continue
                            if t.startswith("~"):
                                t = t[1:]
                            if "/" in t:
                                t = t.split("/", 1)[0]
                            if t.startswith("(") and t.endswith(")"):
                                t = t[1:-1]
                            if _note_to_midi(t) is not None:
                                notes += 1
                        if tr.name.lower() == "drums" and tok in ("K","S","H","O","C","T"):
                            drum_hits += 1

    bass_note_events = 0
    bass_explicit = False
    bass_generated = False
    playback_plan, _ = build_playback_plan(song)
    source_playback_plan, _ = build_playback_plan(source_song)
    abs_cursor = 0
    source_abs_cursor = 0
    source_explicit_bass_counts: List[int] = []
    for sec_inst in source_playback_plan:
        sec = source_song.sections[sec_inst.section_index]
        tracks = sec.tracks
        max_bars = 0
        for tr in tracks.values():
            max_bars = max(max_bars, len(tr.bars))
        bass_track = tracks.get("Bass")
        explicit_events = 0
        if bass_track is not None:
            for bar_index, bar in enumerate(bass_track.bars):
                filtered_cells = [SimpleNamespace(tokens=[tok for tok in _tokens_for_take(cell.tokens, sec_inst.take_number)]) for cell in bar.cells]
                bass_events, _ = _collect_timed_bass_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_unit_ticks,
                    source_abs_cursor + (bar_index * bar_ticks),
                )
                explicit_events += len(bass_events)
        source_explicit_bass_counts.append(explicit_events)
        source_abs_cursor += max_bars * bar_ticks

    for plan_index, sec_inst in enumerate(playback_plan):
        sec = song.sections[sec_inst.section_index]
        tracks = sec.tracks
        max_bars = 0
        for tr in tracks.values():
            max_bars = max(max_bars, len(tr.bars))
        bass_track = tracks.get("Bass")
        explicit_events = 0
        if bass_track is not None:
            for bar_index, bar in enumerate(bass_track.bars):
                filtered_cells = [SimpleNamespace(tokens=[tok for tok in _tokens_for_take(cell.tokens, sec_inst.take_number)]) for cell in bar.cells]
                bass_events, _ = _collect_timed_bass_events_for_bar(
                    filtered_cells,
                    beats_per_bar,
                    quantize,
                    ticks_per_beat,
                    bar_ticks,
                    grid_unit_ticks,
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
            pattern = resolve_bass_pattern(song, sec_inst, bass_track)
            chords_track = tracks.get("Chords") or tracks.get("Chord")
            if pattern is not None and chords_track is not None:
                generated = generate_bass_events_from_chords(
                    sec,
                    pattern,
                    resolve_bass_rhythm(song, sec_inst, bass_track),
                    resolve_bass_octave(song, sec_inst, bass_track),
                    {
                        "take_number": sec_inst.take_number,
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
        "sections": sec_count,
        "tracks": sorted(track_names),
        "bars_total": bars_total,
        "melody_note_events": notes,
        "bass_note_events": bass_note_events,
        "bass_explicit": bass_explicit,
        "bass_generated": bass_generated,
        "drum_hits": drum_hits,
    }
