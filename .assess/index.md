<!-- assess:run_id=20261007120131-f520a0c5 artifact_schema_version=1.3.0 -->
# Assess Wiki Index

_Last updated: 2026-10-07_

Catalog of every hotspot ever flagged by `/assess` in this repo. Status reflects the most recent run.

| File | First Flagged | Last Seen | Status | Latest CCN | Latest LOC |
|------|---------------|-----------|--------|------------|------------|
| `skills/assess/scripts/assess_core.py` | 2026-05-31 | 2026-10-07 | regressed | 238.0 | 1027 |
| `skills/assess/tests/test_assess_core.py` | 2026-05-31 | 2026-10-07 | regressed | 236.0 | 1819 |
| `skills/assess/scripts/complexity-treemap.py` | 2026-05-31 | 2026-10-07 | regressed | 200.0 | 795 |
| `skills/assess/scripts/lib/doc_graph.py` | 2026-05-31 | 2026-10-07 | regressed | 301.0 | 878 |
| `skills/assess/scripts/lib/keyhole_signals.py` | 2026-06-01 | 2026-10-07 | regressed | 249.0 | 806 |
| `skills/assess/tests/test_keyhole_signals.py` | 2026-06-19 | 2026-10-07 | regressed | 208.0 | 1124 |
| `skills/assess/tests/test_complexity_treemap.py` | 2026-09-19 | 2026-10-07 | regressed | 166.0 | 1040 |
| `skills/assess/scripts/lib/wiki_writer.py` | 2026-10-07 | 2026-10-07 | new | 170.0 | 530 |
| `skills/assess/scripts/doc-graph-svg.py` | 2026-05-31 | 2026-10-07 | persistent | 105.0 | 407 |
| `scripts/floor_anchor.py` | 2026-09-19 | 2026-10-07 | persistent | 188.0 | 646 |
| `skills/assess/scripts/lib/doc_staleness.py` | 2026-05-31 | 2026-10-07 | graduated | - | - |
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
- **regressed** - still a hotspot, and getting worse
- **persistent** - still a hotspot, roughly unchanged
- **retired** - the source file was deleted or excluded; the page is kept for history

## How this gets updated

Each `/assess` run reads this file, the prior `complexity-stats.json`, and the latest run output, then rewrites this index. A file the latest run did not rank keeps its last known row, so no hotspot drops out of the catalog. Per-file detail lives in `hotspots/<slug>.md`. Run history lives in `log.md`.
