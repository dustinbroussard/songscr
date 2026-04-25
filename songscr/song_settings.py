from __future__ import annotations

from typing import Optional

from .ast import Song, Tag, Track

KNOWN_BASS_PATTERNS = {"root", "root5", "octave", "walkup", "pedal"}
KNOWN_BASS_RHYTHMS = {"quarters", "eighths"}


def _find_last_tag_value(tags: list[Tag], tag_name: str) -> Optional[str]:
    normalized = tag_name.strip().lower()
    for tag in reversed(tags):
        if tag.name.lower().strip() == normalized:
            return tag.value
    return None


def _section_tags(song: Song, section_instance) -> list[Tag]:
    section_index = getattr(section_instance, "section_index", None)
    if isinstance(section_index, int) and 0 <= section_index < len(song.sections):
        return song.sections[section_index].tags
    return []


def resolve_bass_pattern(song: Song, section_instance, track: Optional[Track]) -> Optional[str]:
    resolved: Optional[str] = None
    for tag_list in (song.tags, _section_tags(song, section_instance), track.tags if track is not None else []):
        value = _find_last_tag_value(tag_list, "bass pattern")
        if value:
            resolved = str(value).strip().lower()
    if resolved in KNOWN_BASS_PATTERNS:
        return resolved
    return None


def resolve_bass_octave(song: Song, section_instance, track: Optional[Track]) -> int:
    resolved = 2
    for tag_list in (song.tags, _section_tags(song, section_instance), track.tags if track is not None else []):
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
    for tag_list in (song.tags, _section_tags(song, section_instance), track.tags if track is not None else []):
        value = _find_last_tag_value(tag_list, "bass rhythm")
        if value is None:
            continue
        candidate = str(value).strip().lower()
        if candidate in KNOWN_BASS_RHYTHMS:
            resolved = candidate
    return resolved
