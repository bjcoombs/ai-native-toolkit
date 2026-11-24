<!-- assess:run_id=20261124070603-7fa74e4a artifact_schema_version=1.43.0 -->
# Hotspot: `skills/ghreport/scripts/ghreport.sh`

_First flagged: 2026-11-24. Last seen: 2026-11-24. Status: new._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | 721 |
| Cyclomatic complexity (file aggregate) | 102 |
| Commits in churn window | 12 |
| Has test file | no |

## History across runs

| Run date | Run | LOC | CCN | Commits | Status |
|----------|-----|-----|-----|---------|--------|
| 2026-11-24 | 7fa74e4a | 721 | 102 | 12 | new |

## Briefing for editing this file

Use this briefing when about to modify `skills/ghreport/scripts/ghreport.sh`:

Hotspot (new). 721 LOC, aggregate cyclomatic complexity 102, 12 commits in churn window. (Briefing refined by LLM via assess_finalize - see Suggested actions below.) Growth profile: monotonic (+997 lines net over 12 commits to this file in 5 months, none a net reduction).

## Suggested actions

- Split along its seams into sourced helper files: GitHub queries, GitLab queries, the settings section, rendering; move-only, tests/test_ghreport*.py unchanged

