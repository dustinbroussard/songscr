# SongScript User Manual

## Purpose

`songscr` is a deterministic, local-first SongScript compiler for turning plain-text song arrangements into:

- MIDI files
- MusicXML files
- AST JSON
- analysis reports
- regression-friendly event dumps

The project also includes a lightweight desktop GUI, `songscr-gui`, for editing, linting, analyzing, and exporting without working directly in the terminal.

This manual is written for end users. It covers installation, file structure, commands, the GUI, common workflows, and troubleshooting.

## Quick Start

### Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

For tests and development tools:

```bash
python -m pip install -e .[dev]
```

### First CLI Run

```bash
songscr lint sample.songscr
songscr render sample.songscr -o sample.mid
```

### First GUI Run

```bash
songscr-gui
```

## Core Concepts

SongScript is organized around:

- global tags
- sections
- tracks
- bars
- tokens inside bar cells

Minimal example:

```songscr
[Key: A minor] [Tempo: 82] [Time Signature: 4/4] [Quantize: 16th]

[Verse]
[Track: Chords]
| Am7 | D7 | Gm7 | C7 |

[Track: Melody]
| A3-- (R) | C4- D4- E4- F4- | G4-- | (R) |

[Track: Drums]
| K . H . S . H . |
```

## File Structure

### Global Tags

Global tags apply to the whole song unless overridden later:

```songscr
[Key: C major] [Tempo: 96] [Time Signature: 4/4] [Quantize: 16th]
```

Common global tags:

- `Key`
- `Tempo`
- `Time Signature`
- `Quantize`
- `Feel`
- `Pocket`
- `Style`
- `Pitch Bend Range`
- `Bass Pattern`
- `Bass Octave`
- `Bass Rhythm`
- `Voicing`
- `Guitar Tuning`
- `Guitar Position`
- `Chord Range`
- `Voice Leading`

### Sections

Sections are named blocks:

```songscr
[Verse]
[Chorus]
[Bridge]
```

### Tracks

Supported common tracks:

- `Chords`
- `Melody`
- `Lyrics`
- `Bass`
- `Drums`

Track headers can be written as:

```songscr
[Track: Melody]
```

Legacy aliases also work:

- `Chord Track:`
- `Melody Track:`
- `Lyrics Track:`
- `Bass Track:`
- `Drums Track:`

### Bars and Cells

Each bar row is pipe-delimited:

```songscr
| Am7 | D7 | Gm7 | C7 |
```

Each bar is split into cells. The number of cells should match either:

- the beat grid
- the quantize grid

depending on the track and notation style.

## Token Reference

### Melody Tokens

Examples:

- `A3`
- `C4-`
- `G4--`
- `(R)` or `R` for rest
- `C4--(mf)` for dynamic marking
- `A3b2` for bend
- `A3v5` for vibrato
- `A3 >> C4` for pitch ramp

Basic meanings:

- trailing `-` characters sustain the note
- `(mf)`, `(f)`, `(pp)` and numeric values like `(96)` affect velocity
- `b` or `bN` adds bend syntax
- `v` or `vN` adds vibrato syntax
- `>>` creates a pitch ramp

### Chord Tokens

Examples:

- `Am7`
- `D7`
- `Eb9`
- `C/E`
- `Bb7alt`

### Drum Tokens

Supported drum symbols:

- `K` kick
- `S` snare
- `H` closed hi-hat
- `O` open hi-hat
- `C` crash
- `T` tom
- `.` empty slot

Example:

```songscr
| K . H . S . H . |
```

### Lyrics Tokens

Lyrics align to melody events.

Special lyric tokens:

- `*` consume a melody note without lyric text
- `_` lyric extender
- trailing `/` marks phrase boundary

### Bracket Groups

Bracket groups allow subdivisions inside one cell:

```songscr
| [C4 D4 E4 F4] |
```

For drums on beat-grid bars:

```songscr
| [K . H .] |
```

## Tag Scope and Override Rules

Tags can appear at four levels:

1. global
2. section
3. track
4. bar cell

The more specific scope wins.

Example:

```songscr
[Bass Octave: 2]

[Verse]
[Bass Octave: 3]

[Track: Bass]
[Bass Octave: 1]
```

In that section and track, the effective `Bass Octave` is `1`.

## CLI Reference

### `songscr lint`

Validate a SongScript file and print warnings or errors.

```bash
songscr lint my_song.songscr
songscr lint my_song.songscr --strict
```

Behavior:

- returns exit code `1` if errors are found
- prints warnings and errors to `stderr`
- `--strict` turns unknown tags into errors instead of warnings

Use this before rendering or exporting.

### `songscr fmt`

Format a SongScript file into canonical layout.

```bash
songscr fmt my_song.songscr
songscr fmt my_song.songscr -o my_song.fmt.songscr
```

Behavior:

- without `-o`, overwrites the input file atomically
- with `-o`, writes to a new file atomically

### `songscr render`

Render SongScript to MIDI.

```bash
songscr render my_song.songscr -o my_song.mid
songscr render my_song.songscr -o my_song.mid --strict
songscr render my_song.songscr -o my_song.mid --seed 1234
```

Notes:

- `-o` is required
- render stops if lint errors are present
- `--seed` is reserved for deterministic behavior control

### `songscr dump-midi`

Render the song and dump a human-readable summary of selected MIDI events.

```bash
songscr dump-midi my_song.songscr
songscr dump-midi my_song.songscr -o my_song.dump.txt
```

Useful for:

- debugging output
- regression testing
- comparing render changes

### `songscr export-ast`

Export the parsed AST as JSON.

```bash
songscr export-ast my_song.songscr
songscr export-ast my_song.songscr -o my_song.ast.json
```

Useful for:

- parser debugging
- tooling integration
- machine inspection of song structure

### `songscr export-musicxml`

Export SongScript to MusicXML.

```bash
songscr export-musicxml my_song.songscr
songscr export-musicxml my_song.songscr -o my_song.musicxml
```

Behavior:

- may emit warnings if a song has melody without chords or chords without melody
- stops on lint or structural errors

### `songscr stats`

Print summary statistics as JSON.

```bash
songscr stats my_song.songscr
```

Typical fields:

- `tempo`
- `time_signature`
- `sections`
- `tracks`
- `bars_total`
- `melody_note_events`
- `bass_note_events`
- `drum_hits`

### `songscr lyrics-report`

Print lyric alignment metrics.

```bash
songscr lyrics-report my_song.songscr
```

Useful for:

- overflow detection
- orphan extender detection
- syllable estimation

### `songscr expand-templates`

Materialize style-generated tracks into explicit SongScript content.

```bash
songscr expand-templates my_song.songscr
songscr expand-templates my_song.songscr -o expanded.songscr
```

Useful for:

- inspecting generated bass or drums
- freezing templates before editing

### `songscr analyze`

Generate a higher-level analysis report.

```bash
songscr analyze my_song.songscr
songscr analyze my_song.songscr --format json
songscr analyze my_song.songscr --format json -o report.json
```

Modes:

- `text` human-readable summary
- `json` machine-readable report

## GUI Manual

Launch:

```bash
songscr-gui
```

### Interface Overview

The GUI has four main areas:

- title bar
- left navigation rail
- main content panel
- output console

Sections:

- `Work`: source editing and primary actions
- `Review`: analysis and stats views
- `Export`: output path management and export actions
- `About`: interface notes

### Work Page

Main components:

- file path field
- editor
- strict lint toggle
- action buttons

Available actions:

- `Open`
- `Save`
- `Format`
- `Lint`
- `Analyze`
- `Stats`
- `Lyrics`

Behavior:

- long-running work executes in background threads
- results appear in the console and review panels
- formatting can update the editor buffer directly

### Review Page

Contains:

- execution summary
- structured stats panel
- detailed analysis panel

Use this page after running:

- `Analyze`
- `Stats`
- `Lyrics`

### Export Page

Available export targets:

- MIDI Render
- MusicXML
- AST JSON
- Event Dump

Each card includes:

- output path field
- `Browse`
- `Run`

### GUI Shortcuts

- `Esc` closes the window
- `Ctrl+S` saves the current editor content

## Common Workflows

### Workflow 1: Validate Then Render

```bash
songscr lint my_song.songscr --strict
songscr render my_song.songscr -o my_song.mid
```

Why:

- catches syntax and structural issues before export

### Workflow 2: Canonicalize a File

```bash
songscr fmt draft.songscr -o draft.clean.songscr
```

Why:

- normalizes tag layout
- makes diffs easier to read

### Workflow 3: Debug Lyrics Alignment

```bash
songscr lyrics-report my_song.songscr
```

Check for:

- `overflow`
- `orphan_extenders`
- unusually high syllable counts

### Workflow 4: Inspect Generated Template Content

```bash
songscr expand-templates style_song.songscr -o style_song.expanded.songscr
```

Why:

- reveals generated bass and drum material
- makes template output editable as plain SongScript

### Workflow 5: Capture Regression Output

```bash
songscr dump-midi my_song.songscr -o my_song.dump.txt
```

Why:

- useful when comparing output before and after code changes

## Examples

### Basic Harmony + Melody + Drums

```songscr
[Key: A minor] [Tempo: 82] [Time Signature: 4/4] [Quantize: 16th]

[Verse]
[Track: Chords]
| Am7 | D7 | Gm7 | C7 |

[Track: Melody]
| A3-- (R) | C4- D4- E4- F4- | G4-- | (R) |

[Track: Drums]
| K . H . S . H . |
```

### Melody Bracket Subdivision

```songscr
[Tempo: 96] [Time Signature: 4/4] [Quantize: 16th]

[Phrase]
[Track: Melody]
| [C4 D4 E4 F4] | G4-- | R | R |
```

### Lyrics Alignment

```songscr
[Tempo: 96] [Time Signature: 4/4]

[Verse]
[Track: Melody]
| C4 | D4 | E4 | F4 |

[Track: Lyrics]
| glo- | ry | _ | shines/ |
```

### Generated Bass Pattern

```songscr
[Tempo: 100] [Time Signature: 4/4] [Bass Pattern: Root5] [Bass Rhythm: Eighths]

[Verse]
[Track: Chords]
| Am7 | Fmaj7 | C | G |
```

Then inspect with:

```bash
songscr expand-templates my_song.songscr
```

## Output Files

### `.mid`

Binary MIDI file for DAWs, notation tools, or playback software.

### `.musicxml`

Text-based MusicXML for notation tools.

### `.json`

AST or analysis output for tooling and inspection.

### `.dump.txt`

Text render dump for debugging and regression comparison.

## Error and Warning Interpretation

### Unknown Tag

Meaning:

- the parser preserved a tag it does not understand

What to do:

- check spelling
- use `--strict` to force failure on unknown tags

### Unexpected Grid Length

Meaning:

- a track bar does not match the expected beat or quantize grid

What to do:

- verify `Time Signature`
- verify `Quantize`
- ensure the number of cells fits the intended notation style

### Invalid Melody Token

Meaning:

- a note or expression token could not be parsed

What to do:

- confirm note spelling like `C4`, `F#3`, `Bb2`
- confirm expression syntax like `A3b2`, `A3v4`, or `A3 >> C4`

### Lyrics Overflow

Meaning:

- more lyric-bearing tokens than melody events exist in the bar

What to do:

- reduce lyric tokens
- add melody events
- use `*` or `_` intentionally where appropriate

## Troubleshooting

### `songscr: command not found`

Cause:

- editable install not active in the current shell

Fix:

```bash
source .venv/bin/activate
python -m pip install -e .
```

### GUI Does Not Start

Cause:

- desktop environment or `tkinter` runtime may be unavailable

Fix:

- verify Python includes `tkinter`
- try importing it directly:

```bash
python - <<'PY'
import tkinter
print("tkinter ok")
PY
```

### Render Fails Even Though Parsing Works

Cause:

- lint or structural planning errors block rendering

Fix:

```bash
songscr lint my_song.songscr --strict
```

Then fix the reported issues before re-running `render` or `export-musicxml`.

### MusicXML Export Warns About Missing Melody or Chords

Meaning:

- the exporter can still run, but one side of the musical content is absent

Fix:

- add the missing track if you need a fuller notation export

## Best Practices

- lint before rendering
- keep files formatted with `songscr fmt`
- use `expand-templates` before hand-editing generated material
- save dump outputs for render regression checks
- prefer one clear section/track structure over implicit defaults
- keep lyric and melody bars aligned early to avoid large cleanup later

## Reference Commands

```bash
songscr lint input.songscr [--strict]
songscr fmt input.songscr [-o output.songscr]
songscr render input.songscr -o output.mid [--strict] [--seed N]
songscr dump-midi input.songscr [-o output.dump.txt]
songscr export-ast input.songscr [-o output.ast.json]
songscr export-musicxml input.songscr [-o output.musicxml]
songscr stats input.songscr
songscr lyrics-report input.songscr
songscr expand-templates input.songscr [-o output.songscr]
songscr analyze input.songscr [--format text|json] [-o output]
songscr-gui
```

## Suggested Next Reads

- [README.md](/home/dustin/Projects/songscr/README.md)
- [sample.songscr](/home/dustin/Projects/songscr/sample.songscr)
- [tests/fixtures/musicxml_basic.songscr](/home/dustin/Projects/songscr/tests/fixtures/musicxml_basic.songscr)
- [tests/fixtures/lyrics_melisma.songscr](/home/dustin/Projects/songscr/tests/fixtures/lyrics_melisma.songscr)
