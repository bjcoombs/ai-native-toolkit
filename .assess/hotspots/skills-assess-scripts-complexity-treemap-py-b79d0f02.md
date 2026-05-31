# Hotspot: `skills/assess/scripts/complexity-treemap.py`

_First flagged: 2026-05-31. Last seen: 2026-05-31. Status: new._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 482 |
| Cyclomatic complexity (file max) | 115.0 |
| Commits in churn window | 9 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-05-31 | 482 | 115.0 | 9 | new |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/complexity-treemap.py`:

Hotspot (new). 482 LOC, max cyclomatic complexity 115.0, 9 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Escape bare % as %% in the --test-pressure help string (argparse rejects it on Python 3.14)
- Add a CLI smoke test that runs --help under 3.14 so CI catches argparse regressions

