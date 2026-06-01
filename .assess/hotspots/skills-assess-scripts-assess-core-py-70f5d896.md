# Hotspot: `skills/assess/scripts/assess_core.py`

_First flagged: 2026-05-31. Last seen: 2026-06-01. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 497 |
| Cyclomatic complexity (file max) | 106.0 |
| Commits in churn window | 15 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-06-01 | 497 | 106.0 | 15 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/assess_core.py`:

Hotspot (persistent). 497 LOC, max cyclomatic complexity 106.0, 15 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Document the orchestrator's dependency on each scripts/lib module so the co-change coupling is intentional and owned
- Keep the # noqa: C901 escape hatch named and explicit; do not rewrite - the worst function (ccn 38) is a sum of small steps

