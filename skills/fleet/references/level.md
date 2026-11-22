## Output: `level.json`

Written with `--levels`, beside `groups.jsonl`: each scanned repository's level result,
keyed by its repository id (a failed scan has no entry), in an envelope of
`level_result_version`, `levels_version`, `toolkit_commit` and `plugin_version`.
`commits` holds, under the same ids, `{head_sha, dirty}`: the commit each result measured
and whether the checkout was dirty (its committed tree was then scored, as in
`repos.jsonl`). The
result (`scripts/lib/level_tally.py` in the assess skill, #744) treats each row level of
the maturity model as one condition, `<row id>.<level>`, and reports:

- `conditions[]`: `id`, `row`, `level`, `grade`, `counted`, `state` (`met`, `unmet`,
  `not_measured`; `not_applicable` and `declined` are reserved) and a `reason` code when
  not measured (`needs_full_run`, `needs_platform`, a block's own code, `block_absent`,
  `no_value`, `type_mismatch`, `degenerate_history`, `no_scored_files` (no file scored,
  so the file-size percentile is not a measurement), or `requires:<id>`);
- `tally`: `met` is the level, the count of counted conditions met (graded measured or
  verified); `measured` counts the counted conditions met or unmet; `not_measured` counts
  every condition not measured. An unknown never counts;
- `counted` and `listed`: the met conditions that count, and the met ones that are only
  listed (config-only, declared, or a signal with no producer class);
- `not_measured[]`: `{id, reason}`;
- `step` and `next_step` (`step`, `threshold`, `blockers[]`), from the model's `[[steps]]`
  (null without them). A Step is held when `tally.met` reaches its threshold and its gates
  hold; a gate reads the state of its row's conditions, not their grade.

`/assess` writes the same result for the same commit as the `level` block of its
run context, from the same function. The file has no date or path in it, so a rerun over
the same commits writes identical bytes. Schema: `references/level.schema.json`.
