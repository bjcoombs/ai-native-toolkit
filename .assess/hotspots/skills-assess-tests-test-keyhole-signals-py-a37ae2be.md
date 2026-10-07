<!-- assess:run_id=20261007204013-4ace52ea artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/tests/test_keyhole_signals.py`

_First flagged: 2026-06-19. Last seen: 2026-10-07. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 1152 |
| Cyclomatic complexity (file aggregate) | 213 |
| Worst function | `test_format_accretion_items_roll_up_and_per_file_lines` (11) |
| Commits in churn window | 17 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 895 | 159.0 | 13 | regressed |
| 2026-10-07 | f520a0c5 | 1124 | 208 | 15 | regressed |
| 2026-10-07 | e950cb1b | 1152 | 213 | 17 | regressed |
| 2026-10-07 | 4ace52ea | 1152 | 213 | 17 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/tests/test_keyhole_signals.py`:

Hotspot (persistent). 1152 LOC, aggregate cyclomatic complexity 213 (worst function `test_format_accretion_items_roll_up_and_per_file_lines` 11), 17 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1665 lines net over 17 commits to this file in 4 months, none a net reduction).

## Suggested actions

- Annotate as a tracked large file; name the finding families as the split seams
- Split only when a change needs to touch most of the file

