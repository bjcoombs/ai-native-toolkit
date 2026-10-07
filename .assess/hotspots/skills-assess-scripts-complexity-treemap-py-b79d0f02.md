<!-- assess:run_id=20261007204013-4ace52ea artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/scripts/complexity-treemap.py`

_First flagged: 2026-05-31. Last seen: 2026-10-07. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 846 |
| Cyclomatic complexity (file aggregate) | 214 |
| Worst function | `lizard_scores` (14) |
| Commits in churn window | 29 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 788 | 199.0 | 22 | regressed |
| 2026-10-07 | f520a0c5 | 795 | 200 | 25 | regressed |
| 2026-10-07 | e950cb1b | 817 | 204 | 26 | regressed |
| 2026-10-07 | 4ace52ea | 846 | 214 | 29 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/complexity-treemap.py`:

Hotspot (regressed). 846 LOC, aggregate cyclomatic complexity 214 (worst function `lizard_scores` 14), 29 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

This file is flagged but outside this run's Top 3. See the report's Top 3 Actions, or run a focused /assess pass for file-specific guidance.
