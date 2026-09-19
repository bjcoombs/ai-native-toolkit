<!-- assess:run_id=20260919114544-b4104782 artifact_schema_version=1.1.0 -->
# Hotspot: `scripts/floor_anchor.py`

_First flagged: 2026-09-19. Last seen: 2026-09-19. Status: new._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 646 |
| Cyclomatic complexity (file max) | 188.0 |
| Commits in churn window | 7 |
| Has test file | yes |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-09-19 | 646 | 188.0 | 7 | new |

## Briefing for editing this file

Use this briefing when about to modify `scripts/floor_anchor.py`:

Hotspot (new). 646 LOC, max cyclomatic complexity 188.0, 7 commits in churn window. Carries 1 stale promissory marker(s) (suppression; oldest survived 6 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1067 LOC, 0 net reductions over 7 commits in 2 months).

## Suggested actions

- Map each FLOOR.md clause this file enforces to the test that pins it; list unpinned clauses (tests and code were added together in cfbae30)
- Give the line-280 noqa S310 a reason in the credited form, batched into an already-planned floor-core pull request

