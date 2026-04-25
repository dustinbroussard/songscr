from __future__ import annotations

from typing import List, Optional, Tuple
import re

from .ast import Bar, Cell, Section, Song, Tag, Track
from .struct import parse_struct_line

TRACK_ALIASES = {
    "Chord Track:": "Chords",
    "Melody Track:": "Melody",
    "Lyrics Track:": "Lyrics",
    "Bass Track:": "Bass",
    "Drums Track:": "Drums",
}

TAG_RE = re.compile(r"^\[(?P<name>[^\]:]+)(?::\s*(?P<value>[^\]]+))?\]$")
SECTION_RE = re.compile(r"^\[(?P<name>[A-Za-z0-9_\- ]+)\]\s*$")
TRACK_RE = re.compile(r"^\[Track:\s*(?P<name>[^\]]+)\]\s*$")
ALT_ENDING_TOKEN_RE = re.compile(r"^\{\d+\}$")

KNOWN_TAGS = {
    "mode", "key", "tempo", "time signature", "quantize", "feel", "swing", "humanize", "velocity rand", "pocket", "capo", "scale", "auto",
    "reverb", "chorus", "delay", "compression", "eq", "track vol", "mute", "solo", "chord voicing", "melody instrument", "drums", "bass instrument",
    "pitch bend range", "bass pattern", "bass octave", "bass rhythm", "style", "drum pattern", "template mode",
    "voicing", "guitar tuning", "guitar position", "chord range", "voice leading",
}


def parse_tags_from_line(line: str, scope: str) -> List[Tag]:
    tags: List[Tag] = []
    for match in re.finditer(r"\[[^\]]+\]", line):
        raw = match.group(0).strip()
        tag_match = TAG_RE.match(raw)
        if tag_match:
            name = tag_match.group("name").strip()
            value = tag_match.group("value")
            tags.append(Tag(name=name, value=value.strip() if value else None, scope=scope))
    return tags


def split_bar_cells(line: str) -> Optional[List[str]]:
    if "|" not in line:
        return None
    stripped = line.strip()
    if not stripped.startswith("|") or stripped.count("|") < 2:
        return None
    parts = [part.strip() for part in stripped.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def filter_tokens_for_take(tokens: List[str], take_number: int) -> List[str]:
    out: List[str] = []
    active_take: Optional[int] = None
    for token in tokens:
        match = ALT_ENDING_TOKEN_RE.match(token)
        if match:
            active_take = int(token[1:-1])
            continue
        if active_take is None or active_take == take_number:
            out.append(token)
    return out


def extract_bar_tags_and_strip(cell_raw: str) -> Tuple[List[Tag], str]:
    tags: List[Tag] = []
    out: List[str] = []
    idx = 0
    for match in re.finditer(r"\[[^\]]+\]", cell_raw):
        raw = match.group(0)
        tag_match = TAG_RE.match(raw)
        is_tag = False
        if tag_match:
            name = tag_match.group("name").strip()
            value = tag_match.group("value")
            if ":" in raw or name.lower() in KNOWN_TAGS:
                is_tag = True
                tags.append(Tag(name=name, value=value.strip() if value else None, scope="bar"))
        out.append(cell_raw[idx:match.start()])
        if not is_tag:
            out.append(raw)
        idx = match.end()
    out.append(cell_raw[idx:])
    return tags, "".join(out).strip()


def tokenize_cell(text: str) -> List[str]:
    tokens: List[str] = []
    idx = 0
    while idx < len(text):
        if text[idx].isspace():
            idx += 1
            continue
        if text[idx] == "[":
            end = text.find("]", idx + 1)
            if end == -1:
                tokens.append(text[idx:].strip())
                break
            tokens.append(text[idx:end + 1].strip())
            idx = end + 1
            continue
        end = idx
        while end < len(text) and not text[end].isspace():
            end += 1
        tokens.append(text[idx:end])
        idx = end
    return tokens


def parse_song(text: str) -> Song:
    song = Song(meta={}, tags=[], sections=[], struct=[], struct_items=[])
    current_section: Optional[Section] = None
    current_track: Optional[Track] = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped == "":
            continue
        if stripped.startswith("#") or stripped.startswith("[Goto:") or stripped.startswith("[Repeat:") or stripped.startswith("[Fade:"):
            song.struct.append(stripped)
            parsed_struct = parse_struct_line(stripped)
            if parsed_struct is not None:
                parsed_struct["section_index"] = len(song.sections) - 1 if song.sections else None
                song.struct_items.append(parsed_struct)
            continue

        section_match = SECTION_RE.match(stripped)
        if section_match:
            current_section = Section(name=section_match.group("name").strip(), tags=[], tracks={})
            song.sections.append(current_section)
            current_track = None
            continue

        if stripped in TRACK_ALIASES:
            if current_section is None:
                current_section = Section(name="Main", tags=[], tracks={})
                song.sections.append(current_section)
            track_name = TRACK_ALIASES[stripped]
            current_track = current_section.tracks.get(track_name) or Track(name=track_name, tags=[], bars=[])
            current_section.tracks[track_name] = current_track
            continue

        track_match = TRACK_RE.match(stripped)
        if track_match:
            if current_section is None:
                current_section = Section(name="Main", tags=[], tracks={})
                song.sections.append(current_section)
            track_name = track_match.group("name").strip()
            current_track = current_section.tracks.get(track_name) or Track(name=track_name, tags=[], bars=[])
            current_section.tracks[track_name] = current_track
            continue

        if stripped.startswith("[") and "]" in stripped and stripped.count("[") >= 1 and stripped.count("]") >= 1:
            scope = "global" if current_section is None else "section" if current_track is None else "track"
            tags = parse_tags_from_line(stripped, scope=scope)
            if tags:
                if current_section is None:
                    song.tags.extend(tags)
                    for tag in tags:
                        key = tag.name.lower().strip()
                        if key in (
                            "key", "tempo", "time signature", "quantize", "feel", "swing", "pocket", "capo", "scale",
                            "humanize", "velocity rand", "pitch bend range", "bass pattern", "bass octave", "bass rhythm",
                            "voicing", "guitar tuning", "guitar position", "chord range", "voice leading",
                        ):
                            song.meta[key] = tag.value if tag.value is not None else True
                elif current_track is None:
                    current_section.tags.extend(tags)
                else:
                    current_track.tags.extend(tags)
                continue

        cells = split_bar_cells(stripped)
        if cells is not None:
            if current_section is None:
                current_section = Section(name="Main", tags=[], tracks={})
                song.sections.append(current_section)
            if current_track is None:
                current_track = current_section.tracks.get("Chords") or Track(name="Chords", tags=[], bars=[])
                current_section.tracks["Chords"] = current_track
            bar = Bar(index=len(current_track.bars) + 1, cells=[])
            for cell_raw in cells:
                inline_tags, without_tags = extract_bar_tags_and_strip(cell_raw)
                bar.cells.append(Cell(raw=cell_raw, tokens=tokenize_cell(without_tags), tags=inline_tags))
            current_track.bars.append(bar)
            continue

    return song


def emit_song(song: Song) -> str:
    def format_tag(tag: Tag) -> str:
        if tag.value is None:
            return f"[{tag.name}]"
        return f"[{tag.name}: {tag.value}]"

    ordered_names = [
        "Mode", "Style", "Template Mode", "Key", "Tempo", "Time Signature", "Quantize", "Feel", "Swing", "Humanize",
        "Velocity Rand", "Pocket", "Capo", "Scale", "Voicing", "Voice Leading", "Guitar Tuning", "Guitar Position",
        "Chord Range", "Pitch Bend Range", "Drum Pattern", "Bass Pattern", "Bass Octave", "Bass Rhythm",
    ]

    def tag_sort_key(tag: Tag) -> Tuple[int, str]:
        try:
            return ordered_names.index(tag.name), ""
        except ValueError:
            return 999, tag.name.lower()

    out: List[str] = []
    if song.tags:
        out.append(" ".join(format_tag(tag) for tag in sorted(song.tags, key=tag_sort_key)))

    track_order = ["Chords", "Melody", "Lyrics", "Bass", "Drums", "Automation"]
    for section in song.sections:
        out.append("")
        out.append(f"[{section.name}]")
        if section.tags:
            out.append(" ".join(format_tag(tag) for tag in sorted(section.tags, key=tag_sort_key)))
        for track_name in sorted(section.tracks.keys(), key=lambda value: (track_order.index(value) if value in track_order else 99, value.lower())):
            track = section.tracks[track_name]
            out.append("")
            out.append(f"[Track: {track.name}]")
            if track.tags:
                out.append(" ".join(format_tag(tag) for tag in sorted(track.tags, key=tag_sort_key)))
            for bar in track.bars:
                parts: List[str] = []
                for cell in bar.cells:
                    cell_text = " ".join(cell.tokens)
                    cell_text = re.sub(r"\s*>>\s*", " >> ", cell_text)
                    if cell.tags:
                        suffix = " ".join(format_tag(tag) for tag in sorted(cell.tags, key=tag_sort_key))
                        cell_text = (cell_text + " " if cell_text else "") + suffix
                    parts.append(cell_text.strip())
                out.append("| " + " | ".join(parts) + " |")

    if song.struct:
        out.append("")
        out.extend(song.struct)
    return "\n".join(out).strip() + "\n"


def format_song(text: str) -> str:
    return emit_song(parse_song(text))
