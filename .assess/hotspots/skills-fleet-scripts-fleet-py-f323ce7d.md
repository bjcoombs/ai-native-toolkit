<!-- assess:run_id=20261124070603-7fa74e4a artifact_schema_version=1.43.0 -->
# Hotspot: `skills/fleet/scripts/fleet.py`

_First flagged: 2026-11-24. Last seen: 2026-11-24. Status: new._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 648 |
| Cyclomatic complexity (file aggregate) | 164 |
| Worst function | `main` (54) |
| Commits in churn window | 39 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-11-24 | 7fa74e4a | 648 | 164 | 39 | new |

## Briefing for editing this file

Use this briefing when about to modify `skills/fleet/scripts/fleet.py`:

Hotspot (new). 648 LOC, aggregate cyclomatic complexity 164 (worst function `main` 54), 39 commits in churn window. Carries 32 stale promissory marker(s) (suppression; oldest survived 37 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Record a reason on the 32 # noqa: E402 lines after the sys.path bootstrap, or replace them with one per-file ignore
- Bring skills/fleet/scripts under the CI ruff and mypy runs (Makefile lint:, tests.yml)

