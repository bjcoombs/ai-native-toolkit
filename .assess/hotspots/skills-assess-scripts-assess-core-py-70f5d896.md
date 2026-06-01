# Hotspot: `skills/assess/scripts/assess_core.py`

_First flagged: 2026-05-31. Last seen: 2026-06-01. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 493 |
| Cyclomatic complexity (file max) | 106.0 |
| Commits in churn window | 14 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-06-01 | 493 | 106.0 | 14 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/assess_core.py`:

Hotspot (persistent). 493 LOC, max cyclomatic complexity 106.0, 14 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Extend the mypy gate to cover assess_core.py (currently scoped to scripts/lib/)
- Add mutation testing to confirm the test suite pins behaviour, not just lines

