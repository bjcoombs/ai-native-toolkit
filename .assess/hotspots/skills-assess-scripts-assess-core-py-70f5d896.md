# Hotspot: `skills/assess/scripts/assess_core.py`

_First flagged: 2026-05-31. Last seen: 2026-06-19. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 635 |
| Cyclomatic complexity (file max) | 137.0 |
| Commits in churn window | 26 |
| Has test file | no |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-06-19 | 635 | 137.0 | 26 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/assess_core.py`:

Hotspot (regressed). 635 LOC, max cyclomatic complexity 137.0, 26 commits in churn window. Carries 3 stale promissory marker(s) (suppression, todo; oldest survived 23 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1089 LOC, 0 net reductions over 26 commits in 1 months).

## Suggested actions

- Extract a cohesive module (finding-assembly or emit helpers) behind the existing tests - it only grows (+1089 net over 26 commits, ~7% deletion)
- Action the stale TODO/promissory marker context near line 680 (fix, ticket, or delete)

