# Hotspot: `skills/assess/scripts/lib/doc_graph.py`

_First flagged: 2026-05-31. Last seen: 2026-06-01. Status: persistent._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 514 |
| Cyclomatic complexity (file max) | 169.0 |
| Commits in churn window | 7 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-06-01 | 514 | 169.0 | 7 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/doc_graph.py`:

Hotspot (persistent). 514 LOC, max cyclomatic complexity 169.0, 7 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- High file-aggregate ccn (169) is many small functions, not a monster - worst single function is ccn 23, under the 15-gate only via # noqa. Leave as-is unless adding behaviour
- If extended, add a characterization test pinning current graph output before refactoring

