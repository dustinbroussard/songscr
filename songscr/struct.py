from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import re

from .ast import Song

_LABEL_RE = re.compile(r"^#([A-Za-z0-9_\-]+)$")
_GOTO_RE = re.compile(r"^\[Goto:\s*#?([A-Za-z0-9_\- ]+)\]\s*$", re.IGNORECASE)
_REPEAT_RE = re.compile(r"^\[Repeat:\s*#?(.+?)\s+x(\d+)\]\s*$", re.IGNORECASE)


@dataclass
class SectionInstance:
    section_index: int
    section_name: str
    instance_number: int
    take_number: int


@dataclass
class StructIssue:
    level: str
    rule: str
    message: str
    token: Optional[str] = None
    expected: Optional[str] = None


def parse_struct_line(line: str) -> Optional[Dict[str, Any]]:
    stripped = line.strip()
    m = _LABEL_RE.match(stripped)
    if m:
        return {"type": "label", "name": m.group(1), "raw": stripped}
    m = _GOTO_RE.match(stripped)
    if m:
        return {"type": "goto", "target": m.group(1).strip(), "raw": stripped}
    m = _REPEAT_RE.match(stripped)
    if m:
        return {"type": "repeat", "target": m.group(1).strip(), "times": int(m.group(2)), "raw": stripped}
    if stripped.startswith("[Fade:"):
        return {"type": "fade", "raw": stripped}
    return None


def _resolve_section_index(song: Song, target: str, label_to_section: Dict[str, int]) -> Optional[int]:
    norm = target.strip().lstrip("#")
    if norm in label_to_section:
        return label_to_section[norm]
    for i, sec in enumerate(song.sections):
        if sec.name.lower() == norm.lower():
            return i
    # convenience alias: #Chorus1 -> Chorus
    base = re.sub(r"\d+$", "", norm)
    if base and base != norm:
        for i, sec in enumerate(song.sections):
            if sec.name.lower() == base.lower():
                return i
    return None


def build_playback_plan(song: Song, max_steps: int = 1000) -> Tuple[List[SectionInstance], List[StructIssue]]:
    issues: List[StructIssue] = []
    plan: List[SectionInstance] = []
    if not song.sections:
        return plan, issues

    section_instance_counts: Dict[int, int] = {}

    def add_section(section_index: int, take_number: int = 1) -> None:
        count = section_instance_counts.get(section_index, 0) + 1
        section_instance_counts[section_index] = count
        plan.append(
            SectionInstance(
                section_index=section_index,
                section_name=song.sections[section_index].name,
                instance_number=count,
                take_number=take_number,
            )
        )

    for si in range(len(song.sections)):
        add_section(si, take_number=1)

    if not song.struct_items:
        return plan, issues

    directives = [it for it in song.struct_items if it.get("type") in ("label", "goto", "repeat")]
    if not directives:
        return plan, issues

    label_to_ip: Dict[str, int] = {}
    label_to_section: Dict[str, int] = {}
    for i, it in enumerate(directives):
        if it.get("type") != "label":
            continue
        name = str(it.get("name", "")).strip()
        if not name:
            continue
        label_to_ip[name] = i
        sec_idx = it.get("section_index")
        if isinstance(sec_idx, int) and 0 <= sec_idx < len(song.sections):
            label_to_section[name] = sec_idx

    ip = 0
    steps = 0
    seen_states = set()
    while 0 <= ip < len(directives):
        state = (ip,)
        if state in seen_states:
            issues.append(
                StructIssue(
                    level="ERROR",
                    rule="structLoop",
                    message="Loop detected while building playback plan.",
                    expected="acyclic struct directives",
                )
            )
            break
        seen_states.add(state)
        steps += 1
        if steps > max_steps:
            issues.append(
                StructIssue(
                    level="ERROR",
                    rule="structLoop",
                    message="Struct execution exceeded safe step limit.",
                    expected=f"<= {max_steps} directive steps",
                )
            )
            break

        it = directives[ip]
        typ = it.get("type")
        if typ == "label":
            ip += 1
            continue
        if typ == "goto":
            target = str(it.get("target", "")).strip()
            sec_idx = _resolve_section_index(song, target, label_to_section)
            if sec_idx is not None:
                for i in range(sec_idx, len(song.sections)):
                    add_section(i, take_number=1)
                ip += 1
                continue
            if target in label_to_ip:
                ip = label_to_ip[target]
                continue
            issues.append(
                StructIssue(
                    level="ERROR",
                    rule="gotoMissing",
                    message="Goto target does not exist.",
                    token=it.get("raw"),
                    expected="existing label or section",
                )
            )
            ip += 1
            continue
        if typ == "repeat":
            target = str(it.get("target", "")).strip()
            times = int(it.get("times", 0))
            sec_idx = _resolve_section_index(song, target, label_to_section)
            if sec_idx is None:
                issues.append(
                    StructIssue(
                        level="ERROR",
                        rule="repeatMissing",
                        message="Repeat target does not exist.",
                        token=it.get("raw"),
                        expected="existing label or section",
                    )
                )
                ip += 1
                continue
            for take in range(1, max(0, times) + 1):
                add_section(sec_idx, take_number=take)
            ip += 1
            continue
        ip += 1

    return plan, issues
