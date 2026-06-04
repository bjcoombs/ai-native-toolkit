# Hotspot: `skills/assess/scripts/lib/doc_graph.py`

_First flagged: 2026-05-31. Last seen: 2026-06-04. Status: persistent._

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
| 2026-06-04 | 514 | 169.0 | 7 | persistent |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/doc_graph.py`:

Hotspot (persistent). 514 LOC, max cyclomatic complexity 169.0, 7 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- Add a coverage floor + mutation pilot to confirm the suite pins behaviour
- Worst function ccn 23 is fenced with # noqa: C901 - keep it annotated, don't pre-emptively split

