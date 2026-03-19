from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class DumpEvent:
    abs_ticks: int
    track: int
    event_type: str
    channel: str
    data: str


def _read_u16_be(data: bytes, pos: int) -> Tuple[int, int]:
    if pos + 2 > len(data):
        raise ValueError("Invalid MIDI: truncated u16")
    return (data[pos] << 8) | data[pos + 1], pos + 2


def _read_u32_be(data: bytes, pos: int) -> Tuple[int, int]:
    if pos + 4 > len(data):
        raise ValueError("Invalid MIDI: truncated u32")
    return (
        (data[pos] << 24)
        | (data[pos + 1] << 16)
        | (data[pos + 2] << 8)
        | data[pos + 3],
        pos + 4,
    )


def _read_varlen(data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise ValueError("Invalid MIDI: truncated varlen")
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            return value, pos
    raise ValueError("Invalid MIDI: varlen too long")


def _is_supported_status_byte(b: int) -> bool:
    if b == 0xFF:
        return True
    msg = b & 0xF0
    return msg in (0x80, 0x90, 0xB0, 0xC0, 0xE0)


def _read_delta_ticks(data: bytes, pos: int) -> Tuple[int, int]:
    """
    Read event delta ticks.

    songscr's MIDI writer currently emits a non-standard multi-byte delta format
    where continuation bits are applied before reversing byte order. This decoder
    intentionally supports that output so dump snapshots are stable.
    """
    if pos >= len(data):
        raise ValueError("Invalid MIDI: truncated delta")

    b0 = data[pos]
    if b0 & 0x80:
        return _read_varlen(data, pos)

    end = pos + 1
    while end < len(data) and end - pos < 4:
        if _is_supported_status_byte(data[end]):
            break
        end += 1

    parts = data[pos:end]
    value = 0
    for i, part in enumerate(parts):
        if i == len(parts) - 1 and len(parts) > 1:
            part &= 0x7F
        value = (value << 7) | part
    return value, end


def _parse_track_events(track_data: bytes, track_index: int) -> List[DumpEvent]:
    out: List[DumpEvent] = []
    pos = 0
    abs_ticks = 0
    running_status = None

    while pos < len(track_data):
        delta, pos = _read_delta_ticks(track_data, pos)
        abs_ticks += delta
        if pos >= len(track_data):
            break

        status_or_data = track_data[pos]
        first_data = None
        if status_or_data & 0x80:
            status = status_or_data
            pos += 1
            if status == 0xFF:
                if pos >= len(track_data):
                    raise ValueError("Invalid MIDI: truncated meta event")
                meta_type = track_data[pos]
                pos += 1
                meta_len, pos = _read_varlen(track_data, pos)
                if pos + meta_len > len(track_data):
                    raise ValueError("Invalid MIDI: truncated meta payload")
                payload = track_data[pos : pos + meta_len]
                pos += meta_len
                running_status = None

                if meta_type == 0x51 and len(payload) == 3:
                    us_per_qn = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                    bpm = int(round(60_000_000 / max(1, us_per_qn)))
                    out.append(DumpEvent(abs_ticks, track_index, "tempo", "-", f"bpm={bpm}"))
                elif meta_type == 0x58 and len(payload) >= 2:
                    num = payload[0]
                    den = 1 << payload[1]
                    out.append(DumpEvent(abs_ticks, track_index, "time_signature", "-", f"{num}/{den}"))
                elif meta_type == 0x05:
                    out.append(DumpEvent(abs_ticks, track_index, "lyric", "-", payload.decode("utf-8", errors="replace")))
                elif meta_type == 0x2F:
                    out.append(DumpEvent(abs_ticks, track_index, "end_of_track", "-", "-"))
                continue

            if status in (0xF0, 0xF7):
                sysex_len, pos = _read_varlen(track_data, pos)
                pos += sysex_len
                running_status = None
                continue

            running_status = status
        else:
            if running_status is None:
                raise ValueError("Invalid MIDI: running status without prior status")
            status = running_status
            first_data = status_or_data
            pos += 1

        msg = status & 0xF0
        channel = str(status & 0x0F)

        if msg in (0xC0, 0xD0):
            if first_data is None:
                if pos >= len(track_data):
                    raise ValueError("Invalid MIDI: truncated channel event")
                d1 = track_data[pos]
                pos += 1
            else:
                d1 = first_data
            if msg == 0xC0:
                out.append(DumpEvent(abs_ticks, track_index, "program_change", channel, f"program={d1}"))
            continue

        if first_data is None:
            if pos + 2 > len(track_data):
                raise ValueError("Invalid MIDI: truncated channel event")
            d1 = track_data[pos]
            d2 = track_data[pos + 1]
            pos += 2
        else:
            if pos >= len(track_data):
                raise ValueError("Invalid MIDI: truncated channel event")
            d1 = first_data
            d2 = track_data[pos]
            pos += 1

        if msg == 0x90:
            out.append(DumpEvent(abs_ticks, track_index, "note_on", channel, f"note={d1} vel={d2}"))
        elif msg == 0x80:
            out.append(DumpEvent(abs_ticks, track_index, "note_off", channel, f"note={d1} vel={d2}"))
        elif msg == 0xB0:
            out.append(DumpEvent(abs_ticks, track_index, "cc", channel, f"cc={d1} val={d2}"))
        elif msg == 0xE0:
            value = d1 | (d2 << 7)
            out.append(DumpEvent(abs_ticks, track_index, "pitchbend", channel, str(value)))

    return out


def parse_midi_dump_events(midi_bytes: bytes) -> List[DumpEvent]:
    pos = 0
    if midi_bytes[:4] != b"MThd":
        raise ValueError("Invalid MIDI: missing MThd header")
    pos += 4

    hdr_len, pos = _read_u32_be(midi_bytes, pos)
    if hdr_len < 6:
        raise ValueError("Invalid MIDI: bad header length")

    _fmt, pos = _read_u16_be(midi_bytes, pos)
    ntrks, pos = _read_u16_be(midi_bytes, pos)
    _division, pos = _read_u16_be(midi_bytes, pos)

    # Skip any extra header bytes.
    pos += hdr_len - 6

    events: List[DumpEvent] = []
    for track_index in range(ntrks):
        if pos + 8 > len(midi_bytes) or midi_bytes[pos : pos + 4] != b"MTrk":
            raise ValueError("Invalid MIDI: missing MTrk chunk")
        pos += 4
        track_len, pos = _read_u32_be(midi_bytes, pos)
        if pos + track_len > len(midi_bytes):
            raise ValueError("Invalid MIDI: truncated track chunk")
        track_data = midi_bytes[pos : pos + track_len]
        pos += track_len
        events.extend(_parse_track_events(track_data, track_index))

    return events


def dump_midi_text(midi_bytes: bytes) -> str:
    events = parse_midi_dump_events(midi_bytes)
    lines = [f"{e.abs_ticks}\t{e.track}\t{e.event_type}\t{e.channel}\t{e.data}" for e in events]
    return "\n".join(lines) + "\n"
