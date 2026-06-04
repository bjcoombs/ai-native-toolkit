# Hotspot: `skills/assess/scripts/assess_core.py`

_First flagged: 2026-05-31. Last seen: 2026-06-04. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 499 |
| Cyclomatic complexity (file max) | 108.0 |
| Commits in churn window | 16 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-06-04 | 499 | 108.0 | 16 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/assess_core.py`:

Hotspot (regressed). 499 LOC, max cyclomatic complexity 108.0, 16 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Document its co-change seam with scripts/lib in lib/README.md
- Widen the mypy gate to cover assess_core.py (currently scripts/lib only)

