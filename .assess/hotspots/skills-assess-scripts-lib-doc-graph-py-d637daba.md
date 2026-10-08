<!-- assess:run_id=20261008155039-de900b5c artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/scripts/lib/doc_graph.py`

_First flagged: 2026-05-31. Last seen: 2026-10-08. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 705 |
| Cyclomatic complexity (file aggregate) | 209 |
| Worst function | `_derive_signals` (15) |
| Commits in churn window | 23 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 851 | 291.0 | 16 | regressed |
| 2026-10-07 | f520a0c5 | 878 | 301 | 17 | regressed |
| 2026-10-07 | e950cb1b | 930 | 309 | 19 | regressed |
| 2026-10-07 | 4ace52ea | 695 | 209 | 20 | persistent |
| 2026-10-08 | 417634ee | 701 | 209 | 22 | persistent |
| 2026-10-08 | d0c68d2e | 705 | 209 | 23 | persistent |
| 2026-10-08 | de900b5c | 705 | 209 | 23 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/doc_graph.py`:

Hotspot (persistent). 705 LOC, aggregate cyclomatic complexity 209 (worst function `_derive_signals` 15), 23 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Pin the survivor clusters in _derive_signals and build_doc_graph (304 of 1,315 mutants survive)

