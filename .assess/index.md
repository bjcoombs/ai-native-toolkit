<!-- assess:run_id=20260919114544-b4104782 artifact_schema_version=1.1.0 -->
# Assess Wiki Index

_Last updated: 2026-09-19_

Catalog of every hotspot ever flagged by `/assess` in this repo. Status reflects the most recent run.

| File | First Flagged | Last Seen | Status | Latest CCN | Latest LOC |
|------|---------------|-----------|--------|------------|------------|
| `skills/assess/scripts/assess_core.py` | 2026-05-31 | 2026-09-19 | regressed | 234.0 | 1020 |
| `skills/assess/tests/test_assess_core.py` | 2026-05-31 | 2026-09-19 | regressed | 200.0 | 1644 |
| `skills/assess/scripts/complexity-treemap.py` | 2026-05-31 | 2026-09-19 | regressed | 199.0 | 788 |
| `skills/assess/scripts/lib/doc_graph.py` | 2026-05-31 | 2026-09-19 | regressed | 291.0 | 851 |
| `skills/assess/scripts/lib/keyhole_signals.py` | 2026-06-01 | 2026-09-19 | regressed | 233.0 | 759 |
| `skills/assess/tests/test_keyhole_signals.py` | 2026-06-19 | 2026-09-19 | regressed | 159.0 | 895 |
| `skills/assess/scripts/doc-graph-svg.py` | 2026-05-31 | 2026-09-19 | regressed | 105.0 | 407 |
| `scripts/floor_anchor.py` | 2026-09-19 | 2026-09-19 | new | 188.0 | 646 |
| `skills/assess/scripts/lib/doc_staleness.py` | 2026-05-31 | 2026-09-19 | regressed | 90.0 | 335 |
| `skills/assess/tests/test_complexity_treemap.py` | 2026-09-19 | 2026-09-19 | new | 130.0 | 934 |
| `skills/assess/scripts/lib/liveness_scan.py` | 2026-05-31 | 2026-09-19 | graduated | - | - |
| `skills/assess/scripts/lib/change_coupling.py` | 2026-06-01 | 2026-09-19 | graduated | - | - |

## Legend

- **active** - in the latest top hotspots list
- **new** - newly entered the hotspot list this run
- **graduated** - was a hotspot, no longer is (good)
- **regressed** - still a hotspot, and getting worse
- **persistent** - still a hotspot, roughly unchanged

## How this gets updated

Each `/assess` run reads this file, the prior `complexity-stats.json`, and the latest run output, then rewrites this index. Per-file detail lives in `hotspots/<slug>.md`. Run history lives in `log.md`.
