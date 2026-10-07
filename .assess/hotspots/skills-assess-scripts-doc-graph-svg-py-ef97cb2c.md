<!-- assess:run_id=20261007185833-e950cb1b artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/scripts/doc-graph-svg.py`

_First flagged: 2026-05-31. Last seen: 2026-10-07. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 407 |
| Cyclomatic complexity (file aggregate) | 105 |
| Worst function | `render` (44) |
| Commits in churn window | 8 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 407 | 105.0 | 8 | regressed |
| 2026-10-07 | f520a0c5 | 407 | 105 | 8 | persistent |
| 2026-10-07 | e950cb1b | 407 | 105 | 8 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/doc-graph-svg.py`:

Hotspot (persistent). 407 LOC, aggregate cyclomatic complexity 105 (worst function `render` 44), 8 commits in churn window. Carries 1 stale promissory marker(s) (suppression; oldest survived 7 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Extract render (lizard ccn 44) into named steps behind byte-identical SVG checks
- Give the noqa: E402 bootstrap import a reason or a shared bootstrap helper

