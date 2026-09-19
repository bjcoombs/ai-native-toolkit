<!-- assess:run_id=20260919114544-b4104782 artifact_schema_version=1.1.0 -->
# Hotspot: `skills/assess/scripts/lib/doc_graph.py`

_First flagged: 2026-05-31. Last seen: 2026-09-19. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 851 |
| Cyclomatic complexity (file max) | 291.0 |
| Commits in churn window | 16 |
| Has test file | yes |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-09-19 | 851 | 291.0 | 16 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/doc_graph.py`:

Hotspot (regressed). 851 LOC, max cyclomatic complexity 291.0, 16 commits in churn window. Carries 3 stale promissory marker(s) (suppression; oldest survived 15 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1323 LOC, 0 net reductions over 16 commits in 4 months).

## Suggested actions

- Give the suppressions at lines 43, 46 and 403 a recorded reason, or remove the need for them
- Decompose build_doc_graph (mccabe 21, waived) behind its existing tests, then remove the noqa C901

