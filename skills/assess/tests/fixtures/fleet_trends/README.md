# Fleet trend fixtures (issues #676, #806)

A fixture run history for `tests/test_fleet_trends.py`:

- `history/acme/<date>/`: synthetic `run.json`, `repos.jsonl` and, for runs that stored Steps,
  `level.json` per dated run. Each group's point is its lowest Step with "N of M measured",
  never a mean: `payments` holds Steps 1, 3 and 3 (mean 2.33, lowest 1). The history has a
  maturity-model version change (`levels-1` to `levels-2`) between 2026-08-20 and 2026-09-20, a
  group with no measured repository in one run (`data`), a group absent from one run (`mobile`),
  a repository not measured (`web` on 2026-10-20), a group name long enough to truncate, a run
  with another window (2026-05-20, not comparable) and a malformed entry (2026-04-20).
- Stored Steps (#941): 2026-06-20 and 2026-07-20 have no `level.json`, as entries kept before it,
  so they are not drawn and the caption names them.
- This run (2026-11-20) is held in memory in `tests/fleet_trends_fixture.py`, as `fleet.py`
  holds it.
- `expected-trend.svg`, `expected-report.md`: the golden render and a report embedding it.

Everything here except this README is generated: never hand-edit or hand-merge it. After a
deliberate change, regenerate from `skills/assess` and review the diff:

```bash
uv run --locked python tests/fleet_trends_fixture.py
```
