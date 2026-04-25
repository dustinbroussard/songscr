from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple

from .core import LintIssue, _grid_unit_ticks, _parse_quantize, _parse_tempo, _parse_time_signature, lint_song, parse_song
from .styles import expand_song_templates
from .struct import build_playback_plan


@dataclass(frozen=True)
class CompiledSong:
    text: str
    source_song: Any
    expanded_song: Any
    lint_issues: List[LintIssue]
    playback_plan: List[Any]
    struct_issues: List[Any]
    tempo: int
    time_signature: Tuple[int, int]
    quantize: int
    ppq: int
    ticks_per_beat: int
    beats_per_bar: float
    bar_ticks: int
    grid_unit_ticks: int


def compile_song(text: str, *, strict: bool = False) -> CompiledSong:
    source_song = parse_song(text)
    expanded_song = expand_song_templates(source_song)
    lint_issues = lint_song(text, strict=strict, song=source_song)
    playback_plan, struct_issues = build_playback_plan(expanded_song)

    num, den = _parse_time_signature(expanded_song)
    tempo = _parse_tempo(expanded_song)
    quantize = _parse_quantize(expanded_song)
    ppq = 480
    ticks_per_beat = ppq
    beats_per_bar = num * (4 / den)
    bar_ticks = int(round(beats_per_bar * ticks_per_beat))

    return CompiledSong(
        text=text,
        source_song=source_song,
        expanded_song=expanded_song,
        lint_issues=lint_issues,
        playback_plan=playback_plan,
        struct_issues=struct_issues,
        tempo=tempo,
        time_signature=(num, den),
        quantize=quantize,
        ppq=ppq,
        ticks_per_beat=ticks_per_beat,
        beats_per_bar=beats_per_bar,
        bar_ticks=bar_ticks,
        grid_unit_ticks=_grid_unit_ticks(ticks_per_beat, quantize),
    )


def lint_errors(compiled: CompiledSong) -> List[LintIssue]:
    return [issue for issue in compiled.lint_issues if issue.level == "ERROR"]


def struct_errors(compiled: CompiledSong) -> List[Any]:
    return [issue for issue in compiled.struct_issues if issue.level == "ERROR"]


def raise_for_compilation_errors(compiled: CompiledSong) -> None:
    errors = lint_errors(compiled)
    if errors:
        msgs = "\n".join(issue.format_line() for issue in errors)
        raise ValueError(f"Lint failed:\n{msgs}")

    planning_errors = struct_errors(compiled)
    if planning_errors:
        msgs = "\n".join(f'ERROR rule="{issue.rule}" {issue.message}' for issue in planning_errors)
        raise ValueError(f"Struct planning failed:\n{msgs}")
