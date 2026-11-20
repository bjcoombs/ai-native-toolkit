# Fleet heat map fixtures (issue #570)

A fixture fleet run for `tests/test_fleet_heatmap.py`:

- `levels.toml`: a two-row maturity model (`org2fa`, top level 1; `teamdocs`, top level 2).
- `groups.jsonl`: the current run, rolled up by `lib/fleet_groups.roll_up` from synthetic
  repository rows. `grpa` reaches every top level, `grpb` is level 0 on both rows, `grpc`
  has an unknown two-factor setting and a failed repository, and a long GitLab path has
  no `shared_tooling` evidence, so both of its rows are unknown.
- `earlier/groups.jsonl`: an earlier run of the same maturity model for `--compare`; `grpd` is
  only in the earlier run.
- `expected.svg`, `expected-compare.svg`: the golden renders.

Everything here except `levels.toml` is generated output: never hand-edit or hand-merge it.
`tests/fleet_heatmap_fixture.py` builds both `groups.jsonl` files (a test fails when the
committed ones differ from a fresh build). After a deliberate change, regenerate from
`skills/assess` and review the diff:

```bash
uv run --locked python tests/fleet_heatmap_fixture.py
F=tests/fixtures/fleet_heatmap
uv run --locked python ../fleet/scripts/heatmap.py $F --levels $F/levels.toml --output $F/expected.svg
uv run --locked python ../fleet/scripts/heatmap.py $F --levels $F/levels.toml --compare $F/earlier --output $F/expected-compare.svg
```
