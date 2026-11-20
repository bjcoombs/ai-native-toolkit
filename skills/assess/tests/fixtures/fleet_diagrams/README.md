# Fleet diagram fixtures (issue #674)

A fixture fleet run for `tests/test_fleet_diagrams.py`:

- `levels.toml`: a maturity model with one org row and two team rows (top levels 2 and 3).
- `repos.jsonl`: 15 synthetic repository rows. Busy and immature (`payments/api`,
  `payments/ledger`, `mobile/ios`), a repository whose name needs escaping, two repositories
  close enough that one label cannot be placed, a failed scan (`mobile/android`), one with no
  commit count or `shared_tooling` block (`mobile/watch`), one that shares nothing
  (`data/notebooks`), and CI pins that drift off the most common ref (`ci/python@v3`,
  `ci/node@v4`).
- `expected-quadrant.svg`, `expected-shared-tooling.svg`, `expected-report.md`: the golden renders.

Everything here except `levels.toml` and this README is generated: never hand-edit or
hand-merge it. After a deliberate change, regenerate from `skills/assess` and review the diff:

```bash
uv run --locked python tests/fleet_diagrams_fixture.py
```
