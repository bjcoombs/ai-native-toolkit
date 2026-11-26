## Output: `level.json`

Written with `--levels`, beside `groups.jsonl`: each scanned repository's level result,
keyed by its repository id (a failed scan has no entry), in an envelope of
`level_result_version`, `levels_version`, `toolkit_commit` and `plugin_version`.
`commits` holds, under the same ids, `{head_sha, dirty}`: the commit each result measured
and whether the checkout was dirty (its committed tree was then scored, as in
`repos.jsonl`); `dirty` is `null` when unknown, and the row's `dirty_reason` says why. The
result (`scripts/lib/scoring/level_tally.py` in the assess skill, #744) treats each row level of
the maturity model as one condition, `<row id>.<level>`, and reports:

- `conditions[]`: `id`, `row`, `level`, `grade`, `counted`, `state` (`met`, `unmet`,
  `not_measured`; `not_applicable` and `declined` are reserved) and a `reason` code when
  not measured (`needs_full_run`, `needs_platform`, a block's own code, `block_absent`,
  `no_value`, `type_mismatch`, `degenerate_history`, `no_scored_files` (no file scored,
  so the file-size percentile is not a measurement), `no_manifests` (no dependency manifest
  read, so there is nothing to lock), or `requires:<id>`). An unmet condition carries
  `requires:<id>` too when a condition it requires is not met;
- `tally`: `met` is the level, the count of counted conditions met (graded measured or
  verified); `measured` counts the counted conditions met or unmet; `not_measured` counts
  every condition not measured. An unknown never counts;
- `counted` and `listed`: the met conditions that count, and the met ones that are only
  listed (config-only, declared, or a signal with no producer class);
- `not_measured[]`: `{id, reason}`;
- `step` and `next_step` (`step`, `blockers[]`), from the model's `[[steps]]` (null without
  them). A Step is held when its gates hold, and nothing else moves it (#1083): the tally
  never does. A gate reads the state of its row's conditions, not their grade, and each
  blocker is a gate (`kind: "gate"`).
- `ceiling_step` and `held_above` (#1374): `ceiling_step` is the highest Step whose gates all
  hold once `next_step`'s blocking gates are met, every Step between held too, so a repository
  at Step 1 that waits only on the instruction file but already holds the Step 3 gate reads 3.
  `held_above` lists the ids of the gates above the current Step that already hold, once each,
  in Step order. `ceiling_step` is null and `held_above` empty when `next_step` is null.
  Unlike the baseline's `held_gates` in `.assess/prior-level.json` (every gate the run
  held), `held_above` leaves out the gates of Steps already held.
- `in_place` (#1334): the practices already in place, in model order, each `{row, level, top,
  gates, why?}`. A row is listed when every condition of it holds (`top`, at its top level) or
  when a gate of a Step already held reads it (`gates`). `why` is the condition at `level`'s
  why. Only counted, met conditions make a row `top`: a row with a condition not measured,
  excluded, unmet or only listed (config-only, declared, demoted by the exit rule) is never
  `top`, so a blind run lists nothing it did not read.

With `--pack` (#1276), each pack's `[[rows]]` are layered onto the model and every condition
names its `source` (the base model or the pack). The envelope then gains `packs`: for each
pack, in pass order, its `name`, `version`, `sha256`, `repos` and `practices[]` with `met`,
`measured` and `not_measured`, the same roll-up `fleet-data.json` carries. A repository
whose `.assess/config.toml` excludes a pack condition is in neither column, so
`measured + not_measured` can be less than `repos`. A hazard level carries
`polarity: "hazard"`, and its `met` counts the repositories free of the harm. Without `--pack`
the file is unchanged.

Each result applies the owner exclusions of the repository's own `.assess/config.toml`
(#910), read from its `owner_exclusions` scan: an excluded condition reads `not_applicable`,
is never counted, and never holds a gate, and the result lists it under `excluded`. A
`--scans` subset without `owner_exclusions` applies none. Fleet-wide exclusions through
`--fleet-config` wait on the method stamp (#952).

The fleet's exit rule (#1086, [`demotions.json`](condition-effect.md#output-demotionsjson)) applies
to this run's results: a demoted condition keeps its state, carries
`disposition: "demoted_by_exit_rule"`, and is listed, never counted. A gate condition is never
demoted, so no Step moves. `/assess` never applies a demotion, so its `level` block for the same
commit can count a condition `level.json` lists.

Apart from demotions, `/assess` writes the same result for the same commit as the `level` block of its
run context, from the same function. The file has no date or path in it, so a rerun over
the same commits writes identical bytes. Schema: `references/level.schema.json`.
