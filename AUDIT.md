# Repository Audit: `songscr`

Audit date: 2026-03-19

## Executive Summary

- Project type: local-first Python CLI application and library for parsing SongScript and rendering MIDI/MusicXML.
- Health score: 77/100
- Test status: 33/33 passing in the checked-in virtualenv
- Dependency security status: `pip-audit` found no known vulnerabilities in auditable Python dependencies
- Highest risks:
  1. High architectural concentration in `songscr/core.py` creates a single regression hotspot.
  2. Cross-module imports of underscore-prefixed helpers create tight coupling and make refactors expensive.
  3. No CI, type checking, linter config, coverage reporting, or pre-commit enforcement.
  4. Repo hygiene is weak: no root `.gitignore`, no license file, no contribution/change docs, and generated artifacts are present in the workspace root.
  5. Local environment drift exists: the editable package metadata points at a different historical checkout than the current workspace.

- Recommended priorities:
  1. Stabilize architecture boundaries around parser/linter/render/analyze.
  2. Add basic engineering guardrails: CI, Ruff, mypy or pyright, coverage, pre-commit.
  3. Clean packaging and repository hygiene.
  4. Preserve current behavior with characterization tests before any refactor.

- Estimated effort:
  - Quick wins: 1-3 days
  - Medium-term hardening: 1-2 weeks
  - Long-term architectural split: 3-6 weeks, incremental

## Phase 1: Foundation & Context

### 1.1 Project Identification

- Type: CLI application with importable package entrypoint.
- Primary stack: Python 3.9+, setuptools build backend, pytest test suite.
- Runtime dependencies: none declared in [pyproject.toml:1](/home/dustin/Projects/songscr/pyproject.toml#L1)
- Entry point: `songscr = "songscr.cli:main"` in [pyproject.toml:14](/home/dustin/Projects/songscr/pyproject.toml#L14)
- Packaging metadata is minimal and lacks an explicit license field in [pyproject.toml:5](/home/dustin/Projects/songscr/pyproject.toml#L5)
- No CI/CD config, Dockerfiles, IaC manifests, or deployment descriptors were present in the workspace.
- No `.git` directory was present, so commit history, code ownership, and change trends could not be audited.

### 1.2 Dependency Analysis

- Build dependency: `setuptools>=68` in [pyproject.toml:2](/home/dustin/Projects/songscr/pyproject.toml#L2)
- Dev dependency: `pytest` in [requirements-dev.txt:1](/home/dustin/Projects/songscr/requirements-dev.txt#L1)
- Installed environment snapshot:
  - `pytest==9.0.2`
  - `setuptools` latest on PyPI as of 2026-03-19: `82.0.1`
  - `pytest` latest on PyPI as of 2026-03-19: `9.0.2`
- Vulnerability scan:
  - Command: `.venv/bin/python -m pip_audit --cache-dir /tmp/pip-audit-cache`
  - Result: no known vulnerabilities found
  - Limitation: local editable package `songscr` is not published on PyPI, so it cannot be audited by `pip-audit`
- Licensing:
  - `pytest` license expression on PyPI: MIT
  - `setuptools` license expression on PyPI: MIT
  - Project license file is absent in the repo root

## Phase 2: Code Quality & Security

### Findings By Severity

#### HIGH: God-module concentration makes core behavior expensive to change

- Evidence:
  - [songscr/core.py](/home/dustin/Projects/songscr/songscr/core.py) is 2058 lines and contains 60 functions.
  - `lint_song()` spans [songscr/core.py:641](/home/dustin/Projects/songscr/songscr/core.py#L641) through [songscr/core.py:1279](/home/dustin/Projects/songscr/songscr/core.py#L1279) with estimated cyclomatic complexity 158.
  - `parse_song()` spans [songscr/core.py:227](/home/dustin/Projects/songscr/songscr/core.py#L227) through [songscr/core.py:328](/home/dustin/Projects/songscr/songscr/core.py#L328).
- Impact:
  - Parser, validator, stats, melody timing, bass timing, and lyrics alignment are all co-located.
  - Changes to one area increase regression risk across unrelated features.
  - This is a classic `God Object` / `Big Ball of Mud` precursor.
- Compliance:
  - Violates common maintainability guidance from OWASP SAMM and general clean-code modularity principles.
- Root cause:
  - MVP functionality accumulated into one convenience module instead of clear domain boundaries.
- Immediate fix:
  - Freeze current behavior with more characterization tests around `lint_song`, lyrics alignment, and struct playback.
  - Extract read-only helper groupings first: tag parsing, melody parsing, lyrics alignment, stats.
- Long-term solution:
  - Split into focused modules such as `parser.py`, `lint.py`, `timing.py`, `lyrics.py`, `tags.py`, `stats.py`.
- Migration path:
  - Step 1: move pure helper functions without changing signatures.
  - Step 2: introduce a small facade in `core.py`.
  - Step 3: switch internal imports gradually.
- Risk:
  - Medium regression risk unless guarded by fixture-based tests.
- Effort:
  - 24-40 hours

#### HIGH: Cross-module use of private helpers creates unstable internal APIs

- Evidence:
  - [songscr/render.py:9](/home/dustin/Projects/songscr/songscr/render.py#L9) imports many underscore-prefixed helpers from `core`.
  - [songscr/analyze.py:9](/home/dustin/Projects/songscr/songscr/analyze.py#L9) imports private parsing/timing helpers from `core`.
  - [songscr/musicxml.py:9](/home/dustin/Projects/songscr/songscr/musicxml.py#L9) does the same.
- Impact:
  - Internal refactors in `core.py` will cascade into render, analysis, and MusicXML export.
  - Private API imports are strong evidence of low cohesion and hidden layer coupling.
- Root cause:
  - Shared logic was reused by reaching into implementation details instead of designing stable module interfaces.
- Immediate fix:
  - Promote a minimal supported internal API module, or relocate shared pure helpers into dedicated utility/domain modules.
- Long-term solution:
  - Enforce module boundaries so render/analyze/export consume parsed domain objects rather than raw private helper functions.
- Migration path:
  - Introduce public helper wrappers, deprecate underscore imports internally, then delete direct imports.
- Risk:
  - Low runtime risk if done incrementally; high maintainability cost if deferred.
- Effort:
  - 12-20 hours

#### MEDIUM: Repeated parse/lint/expand passes add avoidable work and can drift semantically

- Evidence:
  - `render_midi_bytes()` parses and expands once, then separately calls `lint_song(text)` which reparses the same text in [songscr/render.py:71](/home/dustin/Projects/songscr/songscr/render.py#L71).
  - `analyze_song()` parses the source song, reparses for expansion, and separately lints again in [songscr/analyze.py:151](/home/dustin/Projects/songscr/songscr/analyze.py#L151).
- Impact:
  - Work is duplicated on every command invocation.
  - Any future divergence between parser or expander paths can create inconsistent results between render/analyze/lint.
  - Current timings are acceptable, but unnecessary repeated passes become material as the DSL grows.
- Quantified evidence:
  - Baseline median times are roughly 76 ms for lint, 103 ms for analyze-json, 104 ms for render, 111 ms for MusicXML export on sample fixtures.
- Root cause:
  - Each command owns its own pipeline instead of sharing a compiled intermediate representation.
- Immediate fix:
  - Introduce a `CompiledSong` or `ProcessingContext` object that carries parse, expansion, lint, and playback-plan outputs.
- Long-term solution:
  - Standardize a single compilation pipeline with explicit phases.
- Migration path:
  - Start by adding an internal helper used by `render_midi_bytes`, `analyze_song`, and MusicXML export.
- Risk:
  - Low if behavior-preserving tests remain in place.
- Effort:
  - 10-16 hours

#### MEDIUM: Broad CLI exception handling hides failure classes and weakens diagnosability

- Evidence:
  - Broad `except Exception` in [songscr/cli.py:36](/home/dustin/Projects/songscr/songscr/cli.py#L36), [songscr/cli.py:46](/home/dustin/Projects/songscr/songscr/cli.py#L46), and [songscr/cli.py:71](/home/dustin/Projects/songscr/songscr/cli.py#L71)
- Impact:
  - User-facing errors lose type information.
  - Unexpected programmer bugs and expected validation failures are flattened into the same UX.
  - CI failures become harder to triage from logs.
- Root cause:
  - Convenience-oriented CLI wrappers treat all command failures identically.
- Immediate fix:
  - Catch `ValueError` or domain-specific exceptions for user mistakes, and let unexpected exceptions propagate in debug/test contexts.
- Long-term solution:
  - Add explicit error classes such as `SongSyntaxError`, `LintFailure`, `StructPlanError`.
- Migration path:
  - Narrow exception types per command without changing exit-code semantics.
- Risk:
  - Low
- Effort:
  - 4-8 hours

#### MEDIUM: Engineering guardrails are largely absent

- Evidence:
  - No CI files in the workspace.
  - No linter or formatter config files were present.
  - No static type checker config was present.
  - No root `.gitignore`, `LICENSE`, `CONTRIBUTING`, or `CHANGELOG` files were present in the workspace root.
- Impact:
  - Regressions are caught only if contributors manually use the checked-in venv and remember the right commands.
  - Repository hygiene and onboarding are fragile.
- Root cause:
  - The project is still operating like an early solo prototype.
- Immediate fix:
  - Add GitHub Actions for tests, Ruff, and a basic type-check step.
  - Add root `.gitignore` and a license file.
- Long-term solution:
  - Add pre-commit hooks and coverage thresholds.
- Migration path:
  - Start with non-blocking CI checks, then tighten policy after one stable week.
- Risk:
  - Low
- Effort:
  - 8-12 hours

#### LOW: Documentation is adequate for basic usage but incomplete for maintainers

- Evidence:
  - README covers install, commands, and MVP notes in [README.md:1](/home/dustin/Projects/songscr/README.md#L1).
  - README lacks architecture notes, release process, environment conventions, and extension guidance.
- Impact:
  - End-user onboarding is acceptable.
  - Contributor onboarding and future design decisions are under-documented.
- Effort:
  - 3-6 hours

### 2.1 Static Analysis

- Bytecode compilation passed: `.venv/bin/python -m compileall -q songscr tests`
- Complexity hotspots above 10:
  - `lint_song` in `songscr/core.py`: 158
  - `render_midi_bytes` in `songscr/render.py`: 87
  - `analyze_song` in `songscr/analyze.py`: 76
  - `_collect_flattened_content` in `songscr/musicxml.py`: 48
  - `song_stats` in `songscr/core.py`: 34
- No dedicated linter config was available, so ESLint/Flake8/Ruff/Pylint style-policy compliance could not be verified from repo configuration.
- No type-checker config was present, so strict typing posture is currently undefined.

### 2.2 Security Deep Dive

- Secret scan result: no obvious hardcoded credentials detected by regex scan.
- Command injection:
  - No `shell=True`, `os.system`, or `subprocess` use in application code.
- Deserialization:
  - No unsafe `pickle` or `yaml.load` patterns found.
- Input handling:
  - Primary attack surface is local file input through CLI commands in [songscr/cli.py:17](/home/dustin/Projects/songscr/songscr/cli.py#L17).
- Web-specific items:
  - SQLi, CSRF, authz, CORS, API rate limiting, and HTTP header posture are not applicable to this local CLI codebase.
- Residual security risk:
  - Parser robustness and malformed-input handling are the main security-relevant concerns for this project class.

### 2.3 Testing Assessment

- Result: `33 passed in 3.41s`
- Test types present:
  - Unit and characterization/snapshot tests
  - CLI subprocess tests
  - Fixture-based regression tests for rendered dumps and MusicXML
- Quality observations:
  - Strong coverage of DSL examples and output stability.
  - Good emphasis on deterministic snapshots.
  - Missing measured line coverage and mutation score.
- Gap:
  - No CI execution record exists in the workspace.

## Phase 3: Architecture & Design

### 3.1 Structural Analysis

- Current structure is mostly layer-based around output modes and domain helpers.
- Positive:
  - Small package with clear top-level module names.
  - Tests mirror feature areas reasonably well.
- Weakness:
  - `core.py` acts as parser, linter, timing engine, and utility bucket.
  - Consumers reach across layers into parser internals instead of consuming a stable domain interface.

### 3.2 Patterns & Anti-Patterns

- Observed patterns:
  - Command dispatcher in CLI
  - Dataclass-based AST and event models
  - Deterministic snapshot-driven verification
- Anti-patterns detected:
  - God Object / God Module: `songscr/core.py`
  - Shotgun surgery risk: same semantic changes likely touch `core.py`, `render.py`, `analyze.py`, and `musicxml.py`
  - Big Ball of Mud tendency through private helper sharing

### 3.3 Scalability & Performance

- There is no network or database layer, so classic web-scale concerns do not apply.
- Current CLI performance is fine on sample inputs.
- Main performance risk is repeated full-pipeline passes and the growth of monolithic functions.
- No obvious memory leak patterns were found.

## Phase 4: Operational Excellence

### 4.1 DevOps & Deployment

- Not applicable in a traditional service sense: no containers, deployment descriptors, or infrastructure code were present.
- Gap:
  - There is no CI automation for build verification.

### 4.2 Monitoring & Observability

- Runtime observability is minimal by design.
- For a CLI tool, the relevant equivalents would be:
  - deterministic exit codes
  - structured error categories
  - optional debug logging
- Those are only partially implemented today.

### 4.3 Documentation Health

- Present:
  - README with install, command examples, and test command
- Missing:
  - license
  - changelog
  - contributing guide
  - architecture notes
  - ADRs

## Phase 5: Developer Experience

### 5.1 Onboarding & Setup

- README setup is simple.
- The checked-in `.venv` made local verification possible, but that is not a substitute for reproducible setup policy.
- Environment drift evidence:
  - `pip list` reported editable metadata for `songscr` from `/home/dustin/Downloads/Projects/songscr`, while imports resolved from `/home/dustin/Projects/songscr`.
- Impact:
  - Developers can believe they are testing one checkout while metadata still references another.

### 5.2 Tooling Ecosystem

- Package/build choice is appropriate for this project size.
- Missing quality-of-life tooling:
  - Ruff
  - mypy or pyright
  - pre-commit
  - coverage
  - CI caching and matrix testing

## Phase 6: Compliance & Governance

### 6.1 Legal & Licensing

- Dependency licenses reviewed:
  - `pytest`: MIT
  - `setuptools`: MIT
- Project license is not declared in packaging metadata and no root `LICENSE` file exists.
- GDPR/PII concerns are low based on the current local-file CLI scope.

### 6.2 Code Ownership

- Could not be assessed because `.git` history was not present.
- Bus factor appears high by metadata and footprint, but this is an inference, not a verified history-based conclusion.

## Phase 7: Cleaning Protocol

- Safe cleanup candidates:
  - add root `.gitignore`
  - stop relying on checked-in cache artifacts
  - document how to recreate generated sample artifacts
  - centralize shared parse/render helpers behind explicit interfaces

## Phase 8: Verification & Validation

- Reproducibility commands used:
  - `.venv/bin/python -m pytest -q`
  - `.venv/bin/python -m compileall -q songscr tests`
  - `.venv/bin/python -m pip_audit --cache-dir /tmp/pip-audit-cache`
- Performance baseline stored in `PERFORMANCE_BASELINE.json`
- Rollback guidance:
  - Land architectural changes behind characterization tests.
  - Keep each extraction as a separate commit/PR.
  - Verify `render`, `dump-midi`, `analyze`, and `export-musicxml` snapshots after each step.

## Prioritization Matrix

### Quick Wins (1-3 days)

1. Add CI for tests and compile checks.
2. Add root `.gitignore`, license file, and contributor-facing setup notes.
3. Reinstall editable package in the current workspace to remove path drift.
4. Introduce Ruff and a non-blocking type-check step.

### Medium-Term (1-2 weeks)

1. Add coverage reporting and maintain a minimum threshold.
2. Refactor repeated parse/lint/expand flows into one compilation pipeline.
3. Introduce domain-specific exception classes for CLI error handling.

### Long-Term (1+ month)

1. Split `core.py` into parser, lint, timing, lyrics, and stats modules.
2. Remove underscore-helper imports across module boundaries.
3. Consider a stable intermediate representation for all outputs.

