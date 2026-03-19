
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional, Iterable, Dict
import struct

def _varlen(value: int) -> bytes:
    """Encode an int as MIDI variable-length quantity."""
    if value < 0:
        raise ValueError("varlen value must be >= 0")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.reverse()
    return bytes(out)

@dataclass
class MidiEvent:
    delta_ticks: int
    data: bytes

def note_on(delta: int, channel: int, note: int, velocity: int) -> MidiEvent:
    return MidiEvent(delta, bytes([0x90 | (channel & 0x0F), note & 0x7F, velocity & 0x7F]))

def note_off(delta: int, channel: int, note: int, velocity: int = 0) -> MidiEvent:
    return MidiEvent(delta, bytes([0x80 | (channel & 0x0F), note & 0x7F, velocity & 0x7F]))

def cc(delta: int, channel: int, controller: int, value: int) -> MidiEvent:
    return MidiEvent(delta, bytes([0xB0 | (channel & 0x0F), controller & 0x7F, value & 0x7F]))

def pitch_bend(delta: int, channel: int, value: int) -> MidiEvent:
    clamped = max(0, min(16383, int(value)))
    lsb = clamped & 0x7F
    msb = (clamped >> 7) & 0x7F
    return MidiEvent(delta, bytes([0xE0 | (channel & 0x0F), lsb, msb]))

def semitones_to_pitchbend(semitones: float, range_semitones: float = 2) -> int:
    if range_semitones <= 0:
        return 8192
    value = 8192 + int(round((float(semitones) / float(range_semitones)) * 8192))
    return max(0, min(16383, value))

def program_change(delta: int, channel: int, program: int) -> MidiEvent:
    return MidiEvent(delta, bytes([0xC0 | (channel & 0x0F), program & 0x7F]))

def meta_tempo(delta: int, bpm: int) -> MidiEvent:
    # microseconds per quarter note
    us = int(round(60_000_000 / max(1, bpm)))
    data = bytes([0xFF, 0x51, 0x03, (us >> 16) & 0xFF, (us >> 8) & 0xFF, us & 0xFF])
    return MidiEvent(delta, data)

def meta_time_signature(delta: int, num: int, den: int) -> MidiEvent:
    # den as power of 2
    dd = 0
    d = den
    while d > 1 and d % 2 == 0:
        d //= 2
        dd += 1
    # MIDI defaults: metronome=24, thirtyseconds=8
    data = bytes([0xFF, 0x58, 0x04, num & 0xFF, dd & 0xFF, 24, 8])
    return MidiEvent(delta, data)

def meta_lyric(delta: int, text: str) -> MidiEvent:
    payload = text.encode("utf-8")
    data = bytes([0xFF, 0x05]) + _varlen(len(payload)) + payload
    return MidiEvent(delta, data)

def end_of_track(delta: int = 0) -> MidiEvent:
    return MidiEvent(delta, bytes([0xFF, 0x2F, 0x00]))

def build_track(events: List[MidiEvent]) -> bytes:
    blob = bytearray()
    for ev in events:
        blob += _varlen(int(ev.delta_ticks))
        blob += ev.data
    # ensure EOT
    if not events or events[-1].data[:3] != b'\xFF\x2F\x00':
        blob += _varlen(0) + end_of_track(0).data
    return b'MTrk' + struct.pack(">I", len(blob)) + bytes(blob)

def build_midi(tracks: List[bytes], ppq: int = 480) -> bytes:
    fmt = 1 if len(tracks) > 1 else 0
    header = b'MThd' + struct.pack(">IHHH", 6, fmt, len(tracks), ppq)
    return header + b"".join(tracks)
