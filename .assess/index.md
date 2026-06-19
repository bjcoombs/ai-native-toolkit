# Assess Wiki Index

_Last updated: 2026-06-19_

Catalog of every hotspot ever flagged by `/assess` in this repo. Status reflects the most recent run.

| File | First Flagged | Last Seen | Status | Latest CCN | Latest LOC |
|------|---------------|-----------|--------|------------|------------|
| `skills/assess/scripts/assess_core.py` | 2026-05-31 | 2026-06-19 | regressed | 137.0 | 635 |
| `skills/assess/scripts/complexity-treemap.py` | 2026-05-31 | 2026-06-19 | regressed | 133.0 | 558 |
| `skills/assess/tests/test_assess_core.py` | 2026-05-31 | 2026-06-19 | regressed | 109.0 | 1015 |
| `skills/assess/scripts/lib/doc_graph.py` | 2026-05-31 | 2026-06-19 | regressed | 194.0 | 592 |
| `skills/assess/scripts/lib/keyhole_signals.py` | 2026-06-01 | 2026-06-19 | new | 166.0 | 609 |
| `skills/assess/scripts/lib/doc_staleness.py` | 2026-05-31 | 2026-06-19 | regressed | 77.0 | 288 |
| `skills/assess/scripts/doc-graph-svg.py` | 2026-05-31 | 2026-06-19 | persistent | 88.0 | 360 |
| `skills/assess/tests/test_keyhole_signals.py` | 2026-06-19 | 2026-06-19 | new | 105.0 | 574 |
| `skills/assess/scripts/lib/liveness_scan.py` | 2026-05-31 | 2026-06-19 | persistent | 107.0 | 412 |
| `skills/assess/scripts/lib/change_coupling.py` | 2026-06-01 | 2026-06-19 | persistent | 84.0 | 237 |
| `scripts/transform_skill.py` | 2026-06-04 | 2026-06-19 | graduated | - | - |
| `skills/assess/scripts/lib/agent_instructions_grader.py` | 2026-05-31 | 2026-06-19 | graduated | - | - |

## Legend

- **active** - in the latest top hotspots list
- **new** - newly entered the hotspot list this run
- **graduated** - was a hotspot, no longer is (good)
- **regressed** - still a hotspot, and getting worse
- **persistent** - still a hotspot, roughly unchanged

## How this gets updated

Each `/assess` run reads this file, the prior `complexity-stats.json`, and the latest run output, then rewrites this index. Per-file detail lives in `hotspots/<slug>.md`. Run history lives in `log.md`.
