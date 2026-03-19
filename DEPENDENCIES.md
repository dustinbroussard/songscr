# Dependency Review

Audit date: 2026-03-19

## Declared Dependencies

### Runtime

- None declared in [pyproject.toml:12](/home/dustin/Projects/songscr/pyproject.toml#L12)

### Build

| Dependency | Current constraint | Latest stable checked | Status | Notes |
| --- | --- | --- | --- | --- |
| setuptools | `>=68` | `82.0.1` | Behind latest | Build floor is acceptable but old relative to current PyPI release. |

### Development

| Dependency | Current constraint | Installed | Latest stable checked | Status | License |
| --- | --- | --- | --- | --- | --- |
| pytest | unpinned | `9.0.2` | `9.0.2` | Current | MIT |

## Vulnerability Scan

- Tool: `pip-audit 2.10.0`
- Command: `.venv/bin/python -m pip_audit --cache-dir /tmp/pip-audit-cache`
- Result: no known vulnerabilities found
- Limitation: local editable package `songscr` is not on PyPI, so `pip-audit` skips it

## License Posture

- `pytest`: MIT
- `setuptools`: MIT
- Project license: not declared in packaging metadata and no `LICENSE` file found in repo root

## Duplicate / Conflicting Dependencies

- None detected in the declared manifests

## Upgrade Paths

### Recommended Now

1. Keep `pytest` on the current 9.x line.
2. Raise the minimum setuptools floor only if you depend on newer packaging behavior.
3. Add a lock or constraints strategy if this project starts adding runtime dependencies.

### Suggested Commands

```bash
.venv/bin/python -m pip install -U setuptools pytest
.venv/bin/python -m pip check
.venv/bin/python -m pip_audit --cache-dir /tmp/pip-audit-cache
```

## Sources

- PyPI `pytest`: https://pypi.org/project/pytest/
- PyPI `setuptools`: https://pypi.org/project/setuptools/

