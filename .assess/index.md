# Assess Wiki Index

_Last updated: 2026-05-31_

Catalog of every hotspot ever flagged by `/assess` in this repo. Status reflects the most recent run.

| File | First Flagged | Last Seen | Status | Latest CCN | Latest LOC |
|------|---------------|-----------|--------|------------|------------|
| `skills/assess/scripts/assess_core.py` | 2026-05-31 | 2026-05-31 | new | 106.0 | 493 |
| `skills/assess/scripts/lib/doc_graph.py` | 2026-05-31 | 2026-05-31 | new | 169.0 | 514 |
| `skills/assess/scripts/complexity-treemap.py` | 2026-05-31 | 2026-05-31 | new | 115.0 | 482 |
| `skills/assess/tests/test_assess_core.py` | 2026-05-31 | 2026-05-31 | new | 78.0 | 761 |
| `skills/assess/scripts/lib/doc_staleness.py` | 2026-05-31 | 2026-05-31 | new | 69.0 | 245 |
| `skills/assess/tests/test_doc_graph.py` | 2026-05-31 | 2026-05-31 | new | 52.0 | 270 |
| `skills/assess/scripts/lib/liveness_scan.py` | 2026-05-31 | 2026-05-31 | new | 97.0 | 364 |
| `skills/assess/scripts/doc-graph-svg.py` | 2026-05-31 | 2026-05-31 | new | 88.0 | 342 |
| `skills/assess/tests/test_test_pressure.py` | 2026-05-31 | 2026-05-31 | new | 79.0 | 384 |
| `skills/assess/scripts/lib/agent_instructions_grader.py` | 2026-05-31 | 2026-05-31 | new | 70.0 | 255 |

## Legend

- **active** - in the latest top hotspots list
- **graduated** - was a hotspot, no longer is (good)
- **regressed** - still a hotspot, and getting worse
- **persistent** - still a hotspot, roughly unchanged

## How this gets updated

Each `/assess` run reads this file, the prior `complexity-stats.json`, and the latest run output, then rewrites this index. Per-file detail lives in `hotspots/<slug>.md`. Run history lives in `log.md`.
