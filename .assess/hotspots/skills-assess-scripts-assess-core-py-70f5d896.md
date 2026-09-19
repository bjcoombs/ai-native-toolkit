<!-- assess:run_id=20260919114544-b4104782 artifact_schema_version=1.1.0 -->
# Hotspot: `skills/assess/scripts/assess_core.py`

_First flagged: 2026-05-31. Last seen: 2026-09-19. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 1020 |
| Cyclomatic complexity (file max) | 234.0 |
| Commits in churn window | 57 |
| Has test file | yes |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
| 2026-09-19 | 1020 | 234.0 | 57 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/assess_core.py`:

Hotspot (regressed). 1020 LOC, max cyclomatic complexity 234.0, 57 commits in churn window. Carries 3 stale promissory marker(s) (suppression, todo; oldest survived 54 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.)

## Suggested actions

- build_run_context sits at mccabe 15 of 15: extract a block before adding the next signal
- Record a credited reason on the line-879 noqa BLE001 (survived 36 edits)

