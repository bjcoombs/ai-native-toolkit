<!-- assess:run_id=20261008133519-d0c68d2e artifact_schema_version=1.3.0 -->
# Assess Wiki Index

_Last updated: 2026-10-08_

Catalog of every hotspot ever flagged by `/assess` in this repo. Status reflects the most recent run.

| File | First Flagged | Last Seen | Status | Latest CCN | Latest LOC |
|------|---------------|-----------|--------|------------|------------|
| `skills/assess/scripts/complexity-treemap.py` | 2026-05-31 | 2026-10-08 | persistent | 214.0 | 848 |
| `skills/assess/scripts/lib/keyhole_signals.py` | 2026-06-01 | 2026-10-08 | persistent | 247.0 | 864 |
| `skills/assess/scripts/assess_core.py` | 2026-05-31 | 2026-10-08 | persistent | 26.0 | 394 |
| `skills/assess/scripts/lib/doc_graph.py` | 2026-05-31 | 2026-10-08 | persistent | 209.0 | 705 |
| `skills/assess/tests/test_complexity_treemap.py` | 2026-09-19 | 2026-10-08 | persistent | 189.0 | 1227 |
| `skills/assess/scripts/lib/wiki_writer.py` | 2026-10-07 | 2026-10-08 | persistent | 170.0 | 534 |
| `skills/assess/tests/test_assess_core.py` | 2026-05-31 | 2026-10-08 | persistent | 62.0 | 518 |
| `skills/assess/scripts/assess_finalize.py` | 2026-10-08 | 2026-10-08 | persistent | 144.0 | 425 |
| `skills/assess/scripts/lib/doc_staleness.py` | 2026-05-31 | 2026-10-08 | new | 119.0 | 499 |
| `scripts/floor_anchor.py` | 2026-09-19 | 2026-10-08 | persistent | 188.0 | 646 |
| `skills/assess/tests/test_keyhole_signals.py` | 2026-06-19 | 2026-10-08 | graduated | - | - |
| `skills/assess/scripts/doc-graph-svg.py` | 2026-05-31 | 2026-10-07 | graduated | - | - |
| `skills/assess/scripts/lib/liveness_scan.py` | 2026-05-31 | 2026-09-19 | graduated | - | - |
| `skills/assess/scripts/lib/change_coupling.py` | 2026-06-01 | 2026-09-19 | graduated | - | - |
| `scripts/transform_skill.py` | 2026-06-04 | 2026-06-04 | graduated | 44.0 | 165 |
| `skills/assess/scripts/lib/agent_instructions_grader.py` | 2026-05-31 | 2026-06-04 | graduated | 70.0 | 260 |
| `skills/assess/tests/test_doc_graph.py` | 2026-05-31 | 2026-06-01 | graduated | 52.0 | 270 |
| `skills/assess/tests/test_test_pressure.py` | 2026-05-31 | 2026-06-01 | graduated | 79.0 | 384 |

## Legend

- **active** - in the latest top hotspots list
- **new** - newly entered the hotspot list this run
- **graduated** - was a hotspot, no longer is (good)
- **regressed** - still a hotspot, and getting worse. Any of: its worst function rose; its summed complexity rose by more than the worst function fell (an aggregate rise larger than the worst-function fall); or the worst function is flat or unmeasured and the sum or churn rose
- **restructured** - still a hotspot; the sum or churn rose but the worst function fell by at least as much as the sum rose, the shape a split into named helpers leaves (good)
- **persistent** - still a hotspot, roughly unchanged
- **retired** - the source file was deleted or excluded; the page is kept for history

## How this gets updated

Each `/assess` run reads this file, the prior `complexity-stats.json`, and the latest run output, then rewrites this index. A file the latest run did not rank keeps its last known row, so no hotspot drops out of the catalog. Per-file detail lives in `hotspots/<slug>.md`. Run history lives in `log.md`.
