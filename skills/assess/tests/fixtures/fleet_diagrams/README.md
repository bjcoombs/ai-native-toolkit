# Fleet diagram fixtures (issues #674, #675)

A fixture fleet run for `tests/test_fleet_diagrams.py`:

- `levels.toml`: a maturity model with one org row and two team rows (top levels 2 and 3).
- `repos.jsonl`: 15 synthetic repository rows. Busy and immature (`payments/api`,
  `payments/ledger`, `mobile/ios`), a repository whose name needs escaping, two repositories
  close enough that one label cannot be placed, a failed scan (`mobile/android`), one with no
  commit count or `shared_tooling` block (`mobile/watch`), one that shares nothing
  (`data/notebooks`), and CI pins that drift off the most common ref (`ci/python@v3`,
  `ci/node@v4`).
- An earlier run (`EARLIER_ROWS` in `tests/fleet_diagrams_fixture.py`, built in memory and
  compared with the real `roll_up` and `compare`) for the slope chart, which plots each group's
  lowest Step (#806) under this model with three Steps added (`stepped`), the earlier Steps as
  its `level.json` would store them: payments climbs, web slips while its measured set moves
  (`*`), platform and data stay flat, mobile is new; ties on both sides exercise the end-label
  spacing. The report golden draws no slope: this model has no Steps.
- `expected-shared-tooling.svg`, `expected-slope.svg`,
  `expected-report.md`: the golden renders.
- `expected-tag-profile.svg` and `expected-tag-heatmap.svg`: the fleet median tag profile strip
  and the estate tag heatmap (#940) over this run. `expected-tag-profile-states.svg`
  (`tests/fleet_tag_profile_fixture.py`) and `expected-tag-heatmap-full.svg` and
  `expected-tag-heatmap-counts-only.svg` (`tests/fleet_tag_heatmap_fixture.py`) draw every row
  and cell state from hand-built tallies, with names kept and with numbered rows.

Everything here except `levels.toml` and this README is generated: never hand-edit or
hand-merge it. After a deliberate change, regenerate from `skills/assess` and review the diff:

```bash
uv run --locked python tests/fleet_diagrams_fixture.py
uv run --locked python tests/fleet_tag_profile_fixture.py
uv run --locked python tests/fleet_tag_heatmap_fixture.py
```
