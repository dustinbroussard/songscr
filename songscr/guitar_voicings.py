from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import re
from typing import List, Optional, Sequence, Tuple

from .chords import ChordSpec, chord_to_midi_notes, pitch_class_to_midi

STANDARD_GUITAR_TUNING = [40, 45, 50, 55, 59, 64]
STRING_SET_RE = re.compile(r"\{([^}]*)\}")
NOTE_RE = re.compile(r"^([A-G])([#b]?)(-?\d+)$")


@dataclass
class GuitarVoicingResult:
    notes: List[int]
    approx: bool
    string_set: List[int]


def _note_to_midi(note: str) -> Optional[int]:
    match = NOTE_RE.match(note.strip())
    if match is None:
        return None
    letter, accidental, octave_text = match.groups()
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter]
    if accidental == "#":
        base += 1
    elif accidental == "b":
        base -= 1
    midi = 12 * (int(octave_text) + 1) + base
    return midi if 0 <= midi <= 127 else None


def parse_guitar_tuning(tag_value: str) -> List[int]:
    tokens = [token for token in str(tag_value or "").split() if token]
    if len(tokens) != 6:
        raise ValueError("Guitar tuning must contain 6 note names.")
    notes = [_note_to_midi(token) for token in tokens]
    if any(note is None for note in notes):
        raise ValueError("Guitar tuning contains an invalid note.")
    return [int(note) for note in notes]


def parse_string_set_suffix(token: str) -> Tuple[str, Optional[List[int]], Optional[str]]:
    cleaned = str(token or "").strip()
    dynamic_suffix = ""
    dyn_match = re.search(r"\([^)]+\)$", cleaned)
    if dyn_match:
        dynamic_suffix = dyn_match.group(0)
        cleaned = cleaned[:dyn_match.start()].strip()

    match = STRING_SET_RE.search(cleaned)
    if match is None:
        base = cleaned + dynamic_suffix
        return base.strip(), None, None

    raw_spec = match.group(1).strip()
    base = (cleaned[:match.start()] + cleaned[match.end():]).strip() + dynamic_suffix
    if raw_spec == "":
        return base.strip(), None, "empty"

    parts = raw_spec.split("-")
    if any(not part.isdigit() for part in parts):
        return base.strip(), None, "invalid"
    strings = [int(part) for part in parts]
    if any(string < 1 or string > 6 for string in strings):
        return base.strip(), None, "invalid"
    if len(set(strings)) != len(strings):
        return base.strip(), None, "duplicate"
    if any(strings[idx] <= strings[idx + 1] for idx in range(len(strings) - 1)):
        return base.strip(), None, "ordering"
    return base.strip(), strings, None


def _position_fret_window(position_pref: str) -> Tuple[int, int]:
    value = str(position_pref or "mid").strip().lower()
    if value == "open":
        return 0, 5
    if value == "high":
        return 7, 12
    return 4, 9


def _string_index(string_number: int) -> int:
    return 6 - string_number


def _priority_pitch_classes(spec: ChordSpec) -> Tuple[int, Optional[int], Optional[int], Optional[int], List[int]]:
    root_pc = pitch_class_to_midi(spec.root, 0)
    if root_pc is None:
        raise ValueError("Invalid chord root.")
    root_pc %= 12

    third_pc: Optional[int]
    if spec.sus == 2:
        third_pc = (root_pc + 2) % 12
    elif spec.sus == 4:
        third_pc = (root_pc + 5) % 12
    elif spec.quality in ("min", "dim"):
        third_pc = (root_pc + 3) % 12
    else:
        third_pc = (root_pc + 4) % 12

    if spec.quality == "dim" or "b5" in spec.alterations:
        fifth_pc = (root_pc + 6) % 12
    elif spec.quality == "aug" or "#5" in spec.alterations:
        fifth_pc = (root_pc + 8) % 12
    else:
        fifth_pc = (root_pc + 7) % 12

    seventh_pc: Optional[int] = None
    if 7 in spec.extensions:
        if "maj7" in spec.alterations:
            seventh_pc = (root_pc + 11) % 12
        elif "dim7" in spec.alterations:
            seventh_pc = (root_pc + 9) % 12
        else:
            seventh_pc = (root_pc + 10) % 12

    chord_pcs: List[int] = []
    for note in chord_to_midi_notes(spec, root_octave=3):
        pc = note % 12
        if pc not in chord_pcs:
            chord_pcs.append(pc)
    return root_pc, third_pc, seventh_pc, fifth_pc, chord_pcs


def _candidate_shapes() -> List[List[int]]:
    return [
        [5, 4, 3, 2],
        [6, 4, 3, 2],
        [4, 3, 2, 1],
        [6, 5, 4, 3],
    ]


def _preferred_center(position_pref: str) -> float:
    value = str(position_pref or "mid").strip().lower()
    if value == "open":
        return 2.0
    if value == "high":
        return 9.5
    return 6.5


def _score_candidate(
    notes: Sequence[int],
    frets: Sequence[int],
    spec: ChordSpec,
    root_pc: int,
    third_pc: Optional[int],
    seventh_pc: Optional[int],
    fifth_pc: Optional[int],
    chord_pcs: Sequence[int],
    position_pref: str,
    low: Optional[int],
    high: Optional[int],
    target_floor: Optional[int],
) -> Tuple[Tuple[float, ...], bool]:
    pcs = [note % 12 for note in notes]
    unique_pcs = set(pcs)
    slash_pc = None
    if spec.slash_bass:
        slash_note = pitch_class_to_midi(spec.slash_bass, 0)
        if slash_note is not None:
            slash_pc = slash_note % 12

    approx = False
    penalty = 0.0
    if root_pc not in unique_pcs:
        penalty += 400
        approx = True
    if third_pc is not None and third_pc not in unique_pcs:
        penalty += 220
        approx = True
    if seventh_pc is not None and seventh_pc not in unique_pcs:
        penalty += 160
        approx = True
    if fifth_pc is not None and fifth_pc not in unique_pcs:
        penalty += 60
        approx = True
    for pc in chord_pcs:
        if pc not in unique_pcs:
            penalty += 20
            approx = True
    if slash_pc is not None and pcs[0] != slash_pc:
        penalty += 260
        approx = True

    duplicate_penalty = (len(pcs) - len(unique_pcs)) * 10
    if any(pc not in chord_pcs for pc in pcs):
        penalty += 150
        approx = True
    if low is not None and min(notes) < low:
        penalty += 180 + (low - min(notes))
        approx = True
    if high is not None and max(notes) > high:
        penalty += 180 + (max(notes) - high)
        approx = True
    if target_floor is not None and min(notes) < target_floor:
        penalty += 260 + ((target_floor - min(notes)) * 12)

    fret_span = max(frets) - min(frets)
    center_penalty = abs((sum(frets) / len(frets)) - _preferred_center(position_pref))
    low_fret_penalty = sum(1 for fret in frets if fret < 0)

    return (
        penalty,
        duplicate_penalty,
        fret_span,
        center_penalty,
        sum(frets),
        low_fret_penalty,
        tuple(notes),
    ), approx


def generate_guitar_voicing_details(
    chord_spec: ChordSpec,
    tuning: Sequence[int],
    capo: int,
    string_set: Optional[Sequence[int]] = None,
    position_pref: str = "Mid",
    low: Optional[int] = None,
    high: Optional[int] = None,
) -> GuitarVoicingResult:
    if len(tuning) != 6:
        raise ValueError("Guitar tuning must have exactly six strings.")

    root_pc, third_pc, seventh_pc, fifth_pc, chord_pcs = _priority_pitch_classes(chord_spec)
    shapes = [list(string_set)] if string_set is not None else _candidate_shapes()
    fret_min, fret_max = _position_fret_window(position_pref)
    bass_pc_name = chord_spec.slash_bass or chord_spec.root
    target_floor = pitch_class_to_midi(bass_pc_name, 2)
    if target_floor is not None:
        target_floor += int(capo)
        if str(position_pref or "").strip().lower() == "high":
            target_floor += 12
        elif str(position_pref or "").strip().lower() == "mid":
            target_floor += 5

    best_notes: List[int] = []
    best_shape: List[int] = []
    best_score: Optional[Tuple[float, ...]] = None
    best_approx = True

    for shape in shapes:
        per_string_candidates: List[List[Tuple[int, int]]] = []
        for string_number in shape:
            open_note = tuning[_string_index(string_number)] + int(capo)
            candidates: List[Tuple[int, int]] = []
            for fret in range(fret_min, fret_max + 1):
                note = open_note + fret
                if low is not None and note < low - 12:
                    continue
                if high is not None and note > high + 12:
                    continue
                if note % 12 in chord_pcs:
                    candidates.append((note, fret))
            if not candidates:
                for fret in range(fret_min, fret_max + 1):
                    note = open_note + fret
                    candidates.append((note, fret))
            per_string_candidates.append(candidates)

        for combo in product(*per_string_candidates):
            notes = [item[0] for item in combo]
            frets = [item[1] for item in combo]
            if any(notes[idx] >= notes[idx + 1] for idx in range(len(notes) - 1)):
                continue
            score, approx = _score_candidate(
                notes,
                frets,
                chord_spec,
                root_pc,
                third_pc,
                seventh_pc,
                fifth_pc,
                chord_pcs,
                position_pref,
                low,
                high,
                target_floor,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_notes = notes
                best_shape = list(shape)
                best_approx = approx

    return GuitarVoicingResult(notes=best_notes, approx=best_approx or not bool(best_notes), string_set=best_shape)


def generate_guitar_voicing(
    chord_spec: ChordSpec,
    tuning: Sequence[int],
    capo: int,
    string_set: Optional[Sequence[int]] = None,
    position_pref: str = "Mid",
    low: Optional[int] = None,
    high: Optional[int] = None,
) -> List[int]:
    return generate_guitar_voicing_details(
        chord_spec,
        tuning,
        capo,
        string_set=string_set,
        position_pref=position_pref,
        low=low,
        high=high,
    ).notes
