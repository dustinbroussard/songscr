# Refactoring Plan

## Goal

Reduce regression risk in `songscr` without changing the DSL or user-facing command behavior.

## Phase 0: Stabilize Behavior

1. Keep the current snapshot and fixture tests green.
2. Add characterization tests for:
   - malformed tags
   - struct playback edge cases
   - lyrics alignment edge cases
   - MusicXML output from generated tracks

## Phase 1: Introduce Guardrails

1. Add CI for:
   - `.venv/bin/python -m pytest -q`
   - `.venv/bin/python -m compileall -q songscr tests`
   - lint and type-check once configured
2. Add root `.gitignore`.
3. Add a `LICENSE` file and contributor setup notes.
4. Add Ruff and mypy or pyright in non-blocking mode.

## Phase 2: Split Shared Compilation Stages

1. Add an internal `ProcessingContext` or `CompiledSong` object.
2. Compute these stages once:
   - parsed source song
   - expanded song
   - lint issues
   - playback plan
3. Update `render`, `analyze`, and MusicXML export to consume the shared context.

## Phase 3: Decompose `core.py`

Suggested target modules:

- `parser.py`
- `lint.py`
- `timing.py`
- `lyrics.py`
- `stats.py`
- `tags.py`

Rules:

1. Move pure helpers first.
2. Keep `core.py` as a facade during migration.
3. Do not change public command names or output formats while extracting logic.

## Phase 4: Remove Private Cross-Imports

1. Replace underscore-helper imports from `core.py` with stable interfaces.
2. Keep only domain objects and explicit service functions shared between modules.

## Phase 5: Improve CLI Failure Model

1. Introduce typed exceptions for lint, parse, and planning failures.
2. Preserve current exit code behavior.
3. Optionally add `--debug` to print full tracebacks.

## Effort Estimate

- Phase 0-1: 1-3 days
- Phase 2: 2-4 days
- Phase 3-4: 1-3 weeks
- Phase 5: 0.5-1 day

