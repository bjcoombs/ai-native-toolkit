# Fleet heat map fixtures (issue #570)

A fixture fleet run for `tests/test_fleet_heatmap.py`:

- `rubric.toml`: a two-row rubric (`org2fa`, top level 1; `teamdocs`, top level 2).
- `groups.jsonl`: the current run, rolled up by `lib/fleet_groups.roll_up` from synthetic
  repository rows. `grpa` reaches every top level, `grpb` is level 0 on both rows, `grpc`
  has an unknown two-factor setting and a failed repository, and a long GitLab path has
  no `shared_tooling` evidence, so both of its rows are unknown.
- `earlier/groups.jsonl`: an earlier run of the same rubric for `--compare`; `grpd` is
  only in the earlier run.
- `expected.svg`, `expected-compare.svg`: the golden renders.

The goldens are generated output: never hand-edit or hand-merge them. After a deliberate
render change, regenerate from `skills/assess` and review the diff:

```bash
F=tests/fixtures/fleet_heatmap
uv run --locked python ../fleet/scripts/heatmap.py $F --rubric $F/rubric.toml --output $F/expected.svg
uv run --locked python ../fleet/scripts/heatmap.py $F --rubric $F/rubric.toml --compare $F/earlier --output $F/expected-compare.svg
```
