<!-- assess:run_id=20261120124339-d8481c65 artifact_schema_version=1.14.0 -->
# Hotspot: `scripts/floor_check.py`

_First flagged: 2026-11-20. Last seen: 2026-11-20. Status: new._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 629 |
| Cyclomatic complexity (file aggregate) | 199 |
| Worst function | `cmd_markers` (22) |
| Commits in churn window | 7 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-11-20 | d8481c65 | 629 | 199 | 7 | new |

## Briefing for editing this file

Use this briefing when about to modify `scripts/floor_check.py`:

Hotspot (new). 629 LOC, aggregate cyclomatic complexity 199 (worst function `cmd_markers` 22), 7 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1070 lines net over 7 commits to this file in 4 months, none a net reduction).

## Suggested actions

- Extract the per-file branches of cmd_markers in scripts/floor_check.py (fn ccn 22) into named helpers in the same file, behind characterization tests
- Floor core: the PR needs the maintainer's floor-signoff; helpers stay inside the one standalone file

