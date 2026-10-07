<!-- assess:run_id=20261007120131-f520a0c5 artifact_schema_version=1.3.0 -->
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

## Briefing for editing this file

Use this briefing when about to modify `scripts/floor_anchor.py`:

Hotspot (persistent). 646 LOC, aggregate cyclomatic complexity 188 (worst function `_workflow_job_names` 15), 7 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1067 LOC, 0 net reductions over 7 commits in 2 months).

## Suggested actions

- Human review against FLOOR.md: tests were written in the same commit as the code
- Floor file (FLOOR.md clause iii): changes need the maintainer's out-of-band sign-off

