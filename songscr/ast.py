
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Literal, Union

@dataclass
class Tag:
    name: str
    value: Optional[str] = None
    scope: str = "global"  # global|section|track|bar

@dataclass
class Cell:
    raw: str
    tokens: List[str] = field(default_factory=list)
    tags: List[Tag] = field(default_factory=list)

@dataclass
class Bar:
    index: int
    cells: List[Cell] = field(default_factory=list)

@dataclass
class Track:
    name: str
    tags: List[Tag] = field(default_factory=list)
    bars: List[Bar] = field(default_factory=list)

@dataclass
class Section:
    name: str
    tags: List[Tag] = field(default_factory=list)
    tracks: Dict[str, Track] = field(default_factory=dict)

@dataclass
class Song:
    meta: Dict[str, Any] = field(default_factory=dict)     # key, tempo, time_signature, quantize, feel, etc.
    tags: List[Tag] = field(default_factory=list)          # global tags
    sections: List[Section] = field(default_factory=list)
    struct: List[str] = field(default_factory=list)        # reserved, not fully implemented yet
    struct_items: List[Dict[str, Any]] = field(default_factory=list)

def to_jsonable(song: Song) -> Dict[str, Any]:
    def tag_dict(t: Tag) -> Dict[str, Any]:
        return {"name": t.name, "value": t.value, "scope": t.scope}
    def cell_dict(c: Cell) -> Dict[str, Any]:
        return {"raw": c.raw, "tokens": c.tokens, "tags": [tag_dict(t) for t in c.tags]}
    def bar_dict(b: Bar) -> Dict[str, Any]:
        return {"index": b.index, "cells": [cell_dict(c) for c in b.cells]}
    def track_dict(tr: Track) -> Dict[str, Any]:
        return {"name": tr.name, "tags": [tag_dict(t) for t in tr.tags], "bars": [bar_dict(b) for b in tr.bars]}
    def section_dict(s: Section) -> Dict[str, Any]:
        return {"name": s.name, "tags": [tag_dict(t) for t in s.tags], "tracks": {k: track_dict(v) for k, v in s.tracks.items()}}
    return {
        "meta": song.meta,
        "tags": [tag_dict(t) for t in song.tags],
        "sections": [section_dict(s) for s in song.sections],
        "struct": list(song.struct),
        "struct_items": list(song.struct_items),
    }
