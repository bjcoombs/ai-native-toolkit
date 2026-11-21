# fleet-data.json fixtures (issue #703)

Golden `fleet-data.json` documents for `tests/test_fleet_data.py`, built by
`tests/fleet_data_fixture.py` from synthetic repository rows: a three-level GitLab fleet
(`grp`, `grp/sub`, `grp/other`) of four repositories, one failed and one with an
unavailable block, a two-row team maturity model plus an org row, and a units mapping
that adds a `checkout` system view.

- `full.json`: `--full` output.
- `counts-only.json`: the default counts-only output.
- `hashed.json`: counts-only with `--hash-salt fixture-salt`.

Everything here is generated output: never hand-edit or hand-merge it. After a
deliberate change, regenerate from `skills/assess` and review the diff:

```bash
uv run --locked python tests/fleet_data_fixture.py
```
