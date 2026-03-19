from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import re


@dataclass
class ChordSpec:
    root: str
    quality: str
    extensions: List[int]
    alterations: List[str]
    sus: Optional[int]
    add: List[int]
    slash_bass: Optional[str]


_ROOT_RE = re.compile(r'^([A-G])([#b]?)')


def pitch_class_to_midi(pc: str, octave: int) -> Optional[int]:
    m = re.match(r'^([A-G])([#b]?)$', pc)
    if not m:
        return None
    letter, accidental = m.group(1), m.group(2)
    base = {"C":0,"D":2,"E":4,"F":5,"G":7,"A":9,"B":11}[letter]
    if accidental == "#":
        base += 1
    elif accidental == "b":
        base -= 1
    midi = 12 * (octave + 1) + base
    return midi if 0 <= midi <= 127 else None


def parse_chord_symbol(symbol: str) -> Optional[ChordSpec]:
    s = symbol.strip()
    if not s:
        return None

    slash_bass = None
    if "/" in s:
        head, tail = s.rsplit("/", 1)
        if tail:
            slash_bass = tail.strip()
        s = head.strip()

    m = _ROOT_RE.match(s)
    if not m:
        return None
    root = m.group(1) + m.group(2)
    rest = s[m.end():]

    quality = "maj"
    extensions: List[int] = []
    alterations: List[str] = []
    add: List[int] = []
    sus: Optional[int] = None

    # special cases that include both quality and extension
    lower = rest.lower()
    if lower.startswith("m7b5") or lower.startswith("min7b5"):
        quality = "dim"
        extensions.append(7)
        rest = rest[4:] if lower.startswith("m7b5") else rest[6:]
    elif lower.startswith("dim7"):
        quality = "dim"
        extensions.append(7)
        alterations.append("dim7")
        rest = rest[4:]
    elif lower.startswith("maj7"):
        quality = "maj"
        extensions.append(7)
        alterations.append("maj7")
        rest = rest[4:]
    else:
        if lower.startswith("maj"):
            quality = "maj"
            rest = rest[3:]
        elif lower.startswith("min"):
            quality = "min"
            rest = rest[3:]
        elif lower.startswith("m"):
            quality = "min"
            rest = rest[1:]
        elif lower.startswith("dim"):
            quality = "dim"
            rest = rest[3:]
        elif lower.startswith("aug"):
            quality = "aug"
            rest = rest[3:]

    # sus handling
    lower = rest.lower()
    if lower.startswith("sus2"):
        sus = 2
        rest = rest[4:]
    elif lower.startswith("sus4"):
        sus = 4
        rest = rest[4:]
    elif lower.startswith("sus"):
        sus = 4
        rest = rest[3:]

    # parse remaining tokens (extensions/add/alterations)
    while rest:
        lower = rest.lower()
        if lower.startswith("add"):
            m_add = re.match(r'^add(\d+)', lower)
            if not m_add:
                return None
            add.append(int(m_add.group(1)))
            rest = rest[len(m_add.group(0)):]
            continue
        m_alt = re.match(r'^([#b])(5|9)', rest)
        if m_alt:
            alterations.append(m_alt.group(1) + m_alt.group(2))
            rest = rest[len(m_alt.group(0)):]
            continue
        if lower.startswith("alt"):
            rest = rest[3:]
            continue
        m_ext = re.match(r'^(\d+)', rest)
        if m_ext:
            extensions.append(int(m_ext.group(1)))
            rest = rest[len(m_ext.group(1)):]
            continue
        return None

    if slash_bass:
        if pitch_class_to_midi(slash_bass, 3) is None:
            return None

    return ChordSpec(
        root=root,
        quality=quality,
        extensions=extensions,
        alterations=alterations,
        sus=sus,
        add=add,
        slash_bass=slash_bass,
    )


def chord_to_midi_notes(spec: ChordSpec, root_octave: int=3) -> List[int]:
    root_midi = pitch_class_to_midi(spec.root, root_octave)
    if root_midi is None:
        return []

    if spec.sus == 2:
        intervals = [0, 2, 7]
    elif spec.sus == 4:
        intervals = [0, 5, 7]
    else:
        if spec.quality == "min":
            intervals = [0, 3, 7]
        elif spec.quality == "dim":
            intervals = [0, 3, 6]
        elif spec.quality == "aug":
            intervals = [0, 4, 8]
        else:
            intervals = [0, 4, 7]

    if "b5" in spec.alterations or "#5" in spec.alterations:
        new_fifth = 6 if "b5" in spec.alterations else 8
        intervals = [iv for iv in intervals if iv not in (6, 7, 8)]
        intervals.append(new_fifth)

    if 7 in spec.extensions:
        if "maj7" in spec.alterations:
            intervals.append(11)
        elif "dim7" in spec.alterations:
            intervals.append(9)
        else:
            intervals.append(10)

    want_9 = 9 in spec.extensions or 9 in spec.add or "b9" in spec.alterations or "#9" in spec.alterations
    if want_9:
        if "#9" in spec.alterations:
            intervals.append(15)
        elif "b9" in spec.alterations:
            intervals.append(13)
        else:
            intervals.append(14)

    if 11 in spec.extensions or 11 in spec.add:
        intervals.append(17)
    if 13 in spec.extensions or 13 in spec.add:
        intervals.append(21)

    notes = [root_midi + iv for iv in sorted(set(intervals))]
    notes = [n for n in notes if 0 <= n <= 127]

    if spec.slash_bass:
        bass_midi = pitch_class_to_midi(spec.slash_bass, 2)
        if bass_midi is not None and notes:
            if bass_midi >= min(notes):
                alt_bass = pitch_class_to_midi(spec.slash_bass, 3)
                if alt_bass is not None and alt_bass < min(notes):
                    bass_midi = alt_bass
            if bass_midi < min(notes):
                notes = [bass_midi] + notes

    return notes
