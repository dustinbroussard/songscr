# Migration Guide

No major end-user migration is required right now.

The recommended work is an internal architecture cleanup, not a DSL rewrite. Existing `.songscr` files and CLI commands should remain stable while the code is reorganized.

## If You Start The Refactor

1. Keep command names and output formats unchanged.
2. Move implementation behind compatibility facades before deleting old code paths.
3. Re-run snapshot tests after each extraction step.
4. Land parser, lint, render, and MusicXML changes in separate PRs.

## Rollback Plan

1. Revert the most recent extraction PR only.
2. Re-run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m songscr.cli dump-midi sample.songscr -o /tmp/rollback-check.dump.txt
.venv/bin/python -m songscr.cli export-musicxml tests/fixtures/musicxml_basic.songscr -o /tmp/rollback-check.musicxml
```

3. Compare outputs with fixture baselines before relanding.

