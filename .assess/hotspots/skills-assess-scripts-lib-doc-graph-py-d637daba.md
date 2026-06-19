# Hotspot: `skills/assess/scripts/lib/doc_graph.py`

_First flagged: 2026-05-31. Last seen: 2026-06-19. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 592 |
| Cyclomatic complexity (file max) | 194.0 |
| Commits in churn window | 9 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-06-19 | 592 | 194.0 | 9 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/doc_graph.py`:

Hotspot (regressed). 592 LOC, max cyclomatic complexity 194.0, 9 commits in churn window. Carries 3 stale promissory marker(s) (suppression; oldest survived 8 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+907 LOC, 0 net reductions over 9 commits in 0 months).

## Suggested actions

- Split into graph-build vs render - highest file-aggregate ccn (194), worst single function 23; it only grows and carries stale intent
- Clear the `nx = None  # type: ignore` dead-import suppression context

