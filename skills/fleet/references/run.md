## Output: `run.json`

One JSON object per run beside `repos.jsonl`, keys sorted, saying what produced
it. Schema: [`references/run.schema.json`](run.schema.json).

| Key | Value |
|-----|-------|
| `fleet_stamp_version` | Stamp version (1). |
| `toolkit_commit` | The toolkit checkout's `HEAD`; `null` for an install that is not a git checkout. |
| `plugin_version` | `.claude-plugin/plugin.json` `version`. |
| `scan_date` | The UTC date of the run, `YYYY-MM-DD`. |
| `window` | The effective history window (`6mo` when `--window` is not given). |
| `levels_version` | The `--levels` file's `version`; `null` without `--levels`. |
| `counts_only` | `false` with `--full`. |
| `paths_hashed` | `true` with `--hash-paths` or `--hash-salt` (never the salt). |
| `scans` | The `--scans` names, deduplicated and sorted (`--scans a,a` stamps `["a"]`); `null` for all. |
| `fleet_config_sha256` | sha256 of the `--fleet-config` file's canonical form; `null` without one. A comparability key. Absent in a run from before #706, which reads as `null`. |
| `fleet_config_entries` | Entries per section (`ai_usage`, `review`, `platform`): a list counts its items, any other key 1. `null` without `--fleet-config`. Never a pattern or value. |
| `platform` | `true` when `--platform` ran a platform read; `false` without it, and also when `--scans` selects no platform scan (nothing was read). A comparability key. Absent in a run from before #714, which reads as `false`. |
| `managed_settings` | `true` when `--managed-settings` read the Claude Code managed settings of the machine that ran the fleet (#1153); `false` without it. A comparability key. Absent in a run from before #1153, which reads as `false`. |
| `rules_sha256` | sha256 of the rules of the maturity model the run scored with (#952): the loaded `--levels` model without its `version`, row titles and Step names. `null` without `--levels`. Two runs under one `levels_version` with different rules are not compared directly: the history replay re-scores the earlier run, and `--compare` refuses the pair. Absent in a run from before #952, which reads as not recorded: such a run compares by `levels_version` alone, with a note. |
| `collectors` | Every core collector's `id` and `revision` (`skills/assess/checks/core.toml`), sorted by `id` (#952). A revision is bumped when the collector's output changes; a test enforces this for the collectors the fleet golden exercises. A new collector needs no bump: `make regen` records it. Not a comparability key: a change is a note in `compare.json`. Absent in a run from before #952. |
| `repositories` | `selected` (every row), `scanned_ok` (rows `ok`), `failed` (rows `failed`) and `qualifying` (rows `ok` with `active` true). |
| `coverage` | The [`--discovered`](../SKILL.md#coverage---discovered) counts: `discoverable`, `assessed`, `archived_or_empty`, `not_cloned`, `failed_scan`, `excluded_by_filters` and `not_in_listing`. `null` without `--discovered` (coverage unknown), and absent in a run from before #745. Counts only, and not a comparability key. |

The stamp holds no path, salt, timing or input mode: a root run, a list run and
any `--jobs` over unchanged checkouts on the same day write the same bytes. A list
run that leaves a checkout out differs in `repositories` (`selected`, `scanned_ok`) and,
with `--discovered`, in `coverage` (`excluded_by_filters`).
