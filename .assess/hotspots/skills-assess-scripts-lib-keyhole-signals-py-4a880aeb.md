<!-- assess:run_id=20261008133519-d0c68d2e artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/scripts/lib/keyhole_signals.py`

_First flagged: 2026-06-01. Last seen: 2026-10-08. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 864 |
| Cyclomatic complexity (file aggregate) | 247 |
| Worst function | `render_findings_markdown` (11) |
| Commits in churn window | 25 |
| Has test file | no |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 759 | 233.0 | 18 | regressed |
| 2026-10-07 | f520a0c5 | 806 | 249 | 20 | regressed |
| 2026-10-07 | e950cb1b | 885 | 260 | 22 | regressed |
| 2026-10-07 | 4ace52ea | 885 | 260 | 22 | persistent |
| 2026-10-08 | 417634ee | 886 | 260 | 23 | persistent |
| 2026-10-08 | d0c68d2e | 864 | 247 | 25 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/keyhole_signals.py`:

Hotspot (persistent). 864 LOC, aggregate cyclomatic complexity 247 (worst function `render_findings_markdown` 11), 25 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Bring it under disallow_any_generics with TypedDicts for its block shapes (Layer 2)

