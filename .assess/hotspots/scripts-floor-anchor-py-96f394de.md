<!-- assess:run_id=20261007204013-4ace52ea artifact_schema_version=1.3.0 -->
# Hotspot: `scripts/floor_anchor.py`

_First flagged: 2026-09-19. Last seen: 2026-10-07. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 646 |
| Cyclomatic complexity (file aggregate) | 188 |
| Worst function | `_workflow_job_names` (15) |
| Commits in churn window | 7 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 646 | 188.0 | 7 | new |
| 2026-10-07 | f520a0c5 | 646 | 188 | 7 | persistent |
| 2026-10-07 | e950cb1b | 646 | 188 | 7 | persistent |
| 2026-10-07 | 4ace52ea | 646 | 188 | 7 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `scripts/floor_anchor.py`:

Hotspot (persistent). 646 LOC, aggregate cyclomatic complexity 188 (worst function `_workflow_job_names` 15), 7 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1067 lines net over 7 commits to this file in 2 months, none a net reduction).

## Suggested actions

- Maintainer decision on #410: widen floor_core_changed to all seven clause-iii paths or narrow the clause
- Add the clause-derived tests the #410 review lists; floor file, so changes need floor-signoff approval

