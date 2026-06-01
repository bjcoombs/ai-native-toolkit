# Hotspot: `skills/assess/scripts/complexity-treemap.py`

_First flagged: 2026-05-31. Last seen: 2026-06-01. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 482 |
| Cyclomatic complexity (file max) | 115.0 |
| Commits in churn window | 10 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-06-01 | 482 | 115.0 | 10 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/complexity-treemap.py`:

Hotspot (persistent). 482 LOC, max cyclomatic complexity 115.0, 10 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Thin CLI wrapper around lizard/squarify; smoke-tested by design. Add a unit test only if non-trivial logic is added
- Keep build-artifact and generated-code exclude lists in sync with the doc-graph and liveness scans

