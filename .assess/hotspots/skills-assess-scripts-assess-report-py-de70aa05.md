<!-- assess:run_id=20261124070603-7fa74e4a artifact_schema_version=1.43.0 -->
# Hotspot: `skills/assess/scripts/assess_report.py`

_First flagged: 2026-11-20. Last seen: 2026-11-24. Status: regressed._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 454 |
| Cyclomatic complexity (file aggregate) | 143 |
| Worst function | `render_exclusion_disclosure` (19) |
| Commits in churn window | 36 |
| Has test file | yes |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-11-20 | d8481c65 | 393 | 118 | 17 | new |
| 2026-11-20 | 2e2af36e | 393 | 118 | 17 | new |
| 2026-11-20 | d1e8fc2e | 409 | 128 | 18 | regressed |
| 2026-11-24 | 7fa74e4a | 454 | 143 | 36 | regressed |

## Briefing for editing this file

Use this briefing when about to modify `skills/assess/scripts/assess_report.py`:

Hotspot (regressed). 454 LOC, aggregate cyclomatic complexity 143 (worst function `render_exclusion_disclosure` 19), 36 commits in churn window. Carries 3 stale promissory marker(s) (suppression; oldest survived 14 edits to this file). (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+743 lines net over 36 commits to this file in 6 months, none a net reduction).

## Suggested actions

- Record a reason on each # noqa: E402 after the sys.path bootstrap (lines 59-62), inline or as a per-file ignore

