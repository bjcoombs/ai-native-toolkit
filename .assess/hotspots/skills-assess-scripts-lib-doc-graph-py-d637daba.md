<!-- assess:run_id=20261007120131-f520a0c5 artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/scripts/lib/doc_graph.py`

_First flagged: 2026-05-31. Last seen: 2026-10-07. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 878 |
| Cyclomatic complexity (file aggregate) | 301 |
| Worst function | `build_doc_graph` (44) |
| Commits in churn window | 17 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-09-19 | - | 851 | 291.0 | 16 | regressed |
| 2026-10-07 | f520a0c5 | 878 | 301 | 17 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/doc_graph.py`:

Hotspot (regressed). 878 LOC, aggregate cyclomatic complexity 301 (worst function `build_doc_graph` 44), 17 commits in churn window. Carries 3 stale promissory marker(s) (suppression; oldest survived 16 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1386 LOC, 0 net reductions over 17 commits in 4 months).

## Suggested actions

- Give the three stale suppressions (lines 43, 46, 415) a ' - reason' or restructure the imports so they are not needed
- Keep build_doc_graph's noqa: C901 ratchet waiver; extract it behind characterization tests in a separate change

