# SongScript CLI (songscr)

A deterministic, local-first text-to-MIDI compiler for your ChordScript / MelodyScript / RhythmScript ideas.
No AI/LLM integration. If you collaborate with an LLM, you copy/paste text in and out, then `songscr lint` is the judge.

## Install (editable)
From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

## Usage

### Lint
```bash
songscr lint my_song.songscr
songscr lint my_song.songscr --strict
```

### Format
```bash
songscr fmt my_song.songscr
songscr fmt my_song.songscr -o my_song.fmt.songscr
```

### Render to MIDI
```bash
songscr render my_song.songscr -o my_song.mid
songscr render my_song.songscr -o my_song.mid --seed 1234
```

### Dump Rendered MIDI Events
```bash
songscr dump-midi my_song.songscr -o my_song.dump.txt
```

### Export AST
```bash
songscr export-ast my_song.songscr -o my_song.ast.json
```

### Stats
```bash
songscr stats my_song.songscr
```

## MVP notes
- Melody rendering supports simple NOTE tokens, rests, and sustain dashes like `G3--`.
- Chords render as *root notes only* (placeholder for richer voicings later).
- Rhythm rendering supports simple token streams like `K . H . S . H .` in a bar.
- StructScript flattening and advanced expression are reserved for later phases.

## Tests
```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
# songscr
