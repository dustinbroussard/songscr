from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
import re

from .ast import Song

_AUTO_VALUE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 _-]*)\s+([0-9]{1,3})%\s*->\s*([0-9]{1,3})%\s*$",
    re.IGNORECASE,
)


@dataclass
class AutoRamp:
    scope: str  # global|section|track
    param: str
    start_percent: int
    end_percent: int
    cc: Optional[int]
    section_index: Optional[int] = None
    track_name: Optional[str] = None
    raw_value: Optional[str] = None
    order: int = 0


def normalize_param_name(name: str) -> str:
    normalized = name.strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized)
    return normalized


def parse_auto(value: str) -> Tuple[str, int, int]:
    match = _AUTO_VALUE_RE.match(value or "")
    if match is None:
        raise ValueError("Malformed Auto ramp syntax.")
    param_name = normalize_param_name(match.group(1))
    start_percent = int(match.group(2))
    end_percent = int(match.group(3))
    if not 0 <= start_percent <= 100 or not 0 <= end_percent <= 100:
        raise ValueError("Auto ramp percent must be within 0..100.")
    return param_name, start_percent, end_percent


def auto_param_to_cc(param_name: str) -> Optional[int]:
    normalized = normalize_param_name(param_name)
    if normalized == "reverb":
        return 91
    if normalized == "chorus":
        return 93
    if normalized in {"filter_cutoff", "cutoff", "filter"}:
        return 74
    if normalized == "expression":
        return 11
    if normalized == "volume":
        return 7
    if normalized == "pan":
        return 10
    return None


def extract_auto_ramps(song: Song) -> List[AutoRamp]:
    ramps: List[AutoRamp] = []
    order = 0

    def maybe_add(scope: str, raw_value: Optional[str], section_index: Optional[int] = None, track_name: Optional[str] = None) -> None:
        nonlocal order
        if raw_value is None:
            return
        try:
            param, start_percent, end_percent = parse_auto(raw_value)
        except ValueError:
            return
        ramps.append(
            AutoRamp(
                scope=scope,
                param=param,
                start_percent=start_percent,
                end_percent=end_percent,
                cc=auto_param_to_cc(param),
                section_index=section_index,
                track_name=track_name,
                raw_value=raw_value,
                order=order,
            )
        )
        order += 1

    for tag in song.tags:
        if tag.name.strip().lower() == "auto":
            maybe_add("global", tag.value)

    for section_index, section in enumerate(song.sections):
        for tag in section.tags:
            if tag.name.strip().lower() == "auto":
                maybe_add("section", tag.value, section_index=section_index)
        for track in section.tracks.values():
            for tag in track.tags:
                if tag.name.strip().lower() == "auto":
                    maybe_add("track", tag.value, section_index=section_index, track_name=track.name)

    return ramps


def percent_to_cc_value(percent: int) -> int:
    bounded = max(0, min(100, percent))
    return int(round((bounded * 127) / 100))
