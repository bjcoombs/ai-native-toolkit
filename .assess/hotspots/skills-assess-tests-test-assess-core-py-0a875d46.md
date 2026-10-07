<!-- assess:run_id=20261007185833-e950cb1b artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/tests/test_assess_core.py`

_First flagged: 2026-05-31. Last seen: 2026-10-07. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 1843 |
| Cyclomatic complexity (file aggregate) | 240 |
| Worst function | `test_pruned_finding_paths_block_after_rename_and_delete` (16) |
| Commits in churn window | 41 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 1644 | 200.0 | 36 | regressed |
| 2026-10-07 | f520a0c5 | 1819 | 236 | 39 | regressed |
| 2026-10-07 | e950cb1b | 1843 | 240 | 41 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/tests/test_assess_core.py`:

Hotspot (regressed). 1843 LOC, aggregate cyclomatic complexity 240 (worst function `test_pruned_finding_paths_block_after_rename_and_delete` 16), 41 commits in churn window. Carries 2 stale promissory marker(s) (suppression; oldest survived 36 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Resolve the assess_core imports through mypy_path or conftest and drop the type: ignore[import-not-found] at lines 892 and 1179
- Watch band (1,843 LOC): annotate as tracked rather than split pre-emptively

