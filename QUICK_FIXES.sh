#!/usr/bin/env bash
set -euo pipefail

# Refresh the editable install so local package metadata points at this checkout.
.venv/bin/python -m pip install -e .

# Verify the environment and package health.
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q songscr tests
.venv/bin/python -m pytest -q
