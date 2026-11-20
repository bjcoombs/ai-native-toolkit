# Fleet trend fixtures (issue #676)

A fixture run history for `tests/test_fleet_trends.py`:

- `history/acme/<date>/`: synthetic `run.json` and `groups.jsonl` per dated run. Five
  comparable monthly runs, a maturity-model version change (`levels-1` to `levels-2`)
  between 2026-08-20 and 2026-09-20, a group with no level in one run (`data`), a group
  absent from one run (`mobile`), a group name long enough to truncate, a run with another
  window (2026-05-20, not comparable) and a malformed entry (2026-04-20).
- Top levels (#684): only 2026-10-20 records each row's `top_level`, as a run after #684 does.
  The levels-2 entry before it (2026-09-20) takes its top levels from this run's maturity
  model (same version); the levels-1 entries cannot, so they are not drawn and the caption names them.
  Each group also carries an org row in this run, left out of the mean.
- This run (2026-11-20) is held in memory in `tests/fleet_trends_fixture.py`, as `fleet.py`
  holds it.
- `expected-trend.svg`, `expected-report.md`: the golden render and a report embedding it.

Everything here except this README is generated: never hand-edit or hand-merge it. After a
deliberate change, regenerate from `skills/assess` and review the diff:

```bash
uv run --locked python tests/fleet_trends_fixture.py
```
