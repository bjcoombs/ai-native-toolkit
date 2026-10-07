<!-- assess:run_id=20261007185833-e950cb1b artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/tests/test_complexity_treemap.py`

_First flagged: 2026-09-19. Last seen: 2026-10-07. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 1089 |
| Cyclomatic complexity (file aggregate) | 170 |
| Worst function | `test_write_stats_tie_break_by_path_takes_first_ten_in_path_order` (6) |
| Commits in churn window | 21 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 934 | 130.0 | 17 | new |
| 2026-10-07 | f520a0c5 | 1040 | 166 | 20 | regressed |
| 2026-10-07 | e950cb1b | 1089 | 170 | 21 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/tests/test_complexity_treemap.py`:

Hotspot (regressed). 1089 LOC, aggregate cyclomatic complexity 170 (worst function `test_write_stats_tie_break_by_path_takes_first_ten_in_path_order` 6), 21 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1619 lines net over 21 commits to this file in 4 months, none a net reduction).

## Suggested actions

This file is flagged but outside this run's Top 3. See the report's Top 3 Actions, or run a focused /assess pass for file-specific guidance.
