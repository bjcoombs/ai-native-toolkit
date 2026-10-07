<!-- assess:run_id=20261007120131-f520a0c5 artifact_schema_version=1.3.0 -->
# Hotspot: `skills/assess/scripts/lib/wiki_writer.py`

_First flagged: 2026-10-07. Last seen: 2026-10-07. Status: new._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 530 |
| Cyclomatic complexity (file aggregate) | 170 |
| Worst function | `rewrite_log_entry` (13) |
| Commits in churn window | 14 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-10-07 | f520a0c5 | 530 | 170 | 14 | new |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/lib/wiki_writer.py`:

Hotspot (new). 530 LOC, aggregate cyclomatic complexity 170 (worst function `rewrite_log_entry` 13), 14 commits in churn window. Carries 1 stale promissory marker(s) (todo; oldest survived 8 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+1005 LOC, 0 net reductions over 14 commits in 4 months).

## Suggested actions

- Reword the comment at line 20 so the wrapped line no longer opens with 'TODO:'
- Watch band (~900 LOC): annotate as tracked rather than split pre-emptively

