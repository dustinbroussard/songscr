
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .analyze import analyze_song, format_analysis_text
from .core import build_lyrics_alignment_report, emit_song, lint_song, format_song, song_stats
from .render import render_midi_bytes
from .ast import to_jsonable
from .core import parse_song
from .midi_dump import dump_midi_text
from .musicxml import export_musicxml, export_musicxml_warnings
from .styles import expand_song_templates

def cmd_lint(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    issues = lint_song(text, filename=args.input, strict=args.strict)
    errs = [i for i in issues if i.level == "ERROR"]
    warns = [i for i in issues if i.level == "WARN"]
    for i in warns + errs:
        stream = sys.stderr if i.level in ("ERROR","WARN") else sys.stdout
        print(i.format_line(file=args.input), file=stream)
    return 1 if errs else 0

def cmd_fmt(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    formatted = format_song(text)
    out_path = Path(args.output) if args.output else Path(args.input)
    out_path.write_text(formatted, encoding="utf-8")
    return 0

def cmd_render(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    try:
        midi_bytes = render_midi_bytes(text, seed=args.seed, strict=args.strict)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    Path(args.output).write_bytes(midi_bytes)
    return 0

def cmd_dump_midi(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    try:
        midi_bytes = render_midi_bytes(text)
        dumped = dump_midi_text(midi_bytes)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(dumped, encoding="utf-8")
    else:
        print(dumped, end="")
    return 0

def cmd_export_ast(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    song = parse_song(text)
    payload = to_jsonable(song)
    out_str = json.dumps(payload, indent=2)
    if args.output:
        Path(args.output).write_text(out_str, encoding="utf-8")
    else:
        print(out_str)
    return 0

def cmd_export_musicxml(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    try:
        for warning in export_musicxml_warnings(text):
            print(f"WARN {warning}", file=sys.stderr)
        xml_text = export_musicxml(text)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(xml_text, encoding="utf-8")
    else:
        print(xml_text, end="")
    return 0

def cmd_stats(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    stats = song_stats(text)
    print(json.dumps(stats, indent=2))
    return 0

def cmd_lyrics_report(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    song = expand_song_templates(parse_song(text))
    report = build_lyrics_alignment_report(song)
    for section in report:
        print(
            f"{section.section_name}#{section.section_instance}: "
            f"melody_notes={section.melody_event_count} "
            f"lyric_tokens={section.lyric_token_count} "
            f"overflow={section.overflow_count} "
            f"orphan_extenders={section.orphan_extenders} "
            f"estimated_syllables={section.estimated_syllables}"
        )
        for bar in section.bars:
            print(
                f"  bar {bar.bar_index}: "
                f"melody_notes={bar.melody_event_count} "
                f"lyric_tokens={bar.lyric_token_count} "
                f"overflow={bar.overflow_count} "
                f"orphan_extenders={bar.orphan_extenders} "
                f"estimated_syllables={bar.estimated_syllables}"
            )
    return 0

def cmd_expand_templates(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    song = parse_song(text)
    expanded = expand_song_templates(song)
    expanded_text = format_song(emit_song(expanded))
    if args.output:
        Path(args.output).write_text(expanded_text, encoding="utf-8")
    else:
        print(expanded_text, end="")
    return 0

def cmd_analyze(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    analysis = analyze_song(text)
    if args.format == "json":
        output = json.dumps(analysis, indent=2, sort_keys=True) + "\n"
    else:
        output = format_analysis_text(analysis)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="songscr", description="SongScript compiler: lint, fmt, render MIDI, dump MIDI events, export AST.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_lint = sub.add_parser("lint", help="Validate a .songscr file and print warnings/errors.")
    p_lint.add_argument("input")
    p_lint.add_argument("--strict", action="store_true", help="Treat unknown tags as errors.")
    p_lint.set_defaults(func=cmd_lint)

    p_fmt = sub.add_parser("fmt", help="Format a .songscr file into canonical style.")
    p_fmt.add_argument("input")
    p_fmt.add_argument("-o", "--output", help="Output file (default: overwrite input).")
    p_fmt.set_defaults(func=cmd_fmt)

    p_render = sub.add_parser("render", help="Render a .songscr file to a MIDI file.")
    p_render.add_argument("input")
    p_render.add_argument("-o", "--output", required=True, help="Output .mid path.")
    p_render.add_argument("--seed", type=int, default=None, help="Deterministic seed for humanize/velocity rand (reserved).")
    p_render.add_argument("--strict", action="store_true", help="Treat unknown tags as errors.")
    p_render.set_defaults(func=cmd_render)

    p_dump = sub.add_parser("dump-midi", help="Render and dump selected MIDI events for regression testing.")
    p_dump.add_argument("input")
    p_dump.add_argument("-o", "--output", help="Output event dump text path.")
    p_dump.set_defaults(func=cmd_dump_midi)

    p_ast = sub.add_parser("export-ast", help="Export parsed AST as JSON.")
    p_ast.add_argument("input")
    p_ast.add_argument("-o", "--output")
    p_ast.set_defaults(func=cmd_export_ast)

    p_musicxml = sub.add_parser("export-musicxml", help="Export a .songscr file as MusicXML.")
    p_musicxml.add_argument("input")
    p_musicxml.add_argument("-o", "--output")
    p_musicxml.set_defaults(func=cmd_export_musicxml)

    p_stats = sub.add_parser("stats", help="Print quick stats about a .songscr file.")
    p_stats.add_argument("input")
    p_stats.set_defaults(func=cmd_stats)

    p_lyrics = sub.add_parser("lyrics-report", help="Print lyric alignment and syllable report.")
    p_lyrics.add_argument("input")
    p_lyrics.set_defaults(func=cmd_lyrics_report)

    p_expand = sub.add_parser("expand-templates", help="Materialize generated style/template tracks into SongScript.")
    p_expand.add_argument("input")
    p_expand.add_argument("-o", "--output")
    p_expand.set_defaults(func=cmd_expand_templates)

    p_analyze = sub.add_parser("analyze", help="Analyze a .songscr file and print a deterministic report.")
    p_analyze.add_argument("input")
    p_analyze.add_argument("--format", choices=("text", "json"), default="text")
    p_analyze.add_argument("-o", "--output")
    p_analyze.set_defaults(func=cmd_analyze)

    return p

def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
