## Output: `groups.jsonl`

One line per group (a repository row's `group`), sorted by group, keys sorted.
Schema: [`references/groups.schema.json`](groups.schema.json).
Logic: `skills/assess/scripts/lib/fleet/fleet_groups.py`.

| Key | Value |
|-----|-------|
| `fleet_group_version` | Row envelope version (1). |
| `group` | The group id. |
| `repos`, `scanned`, `failed` | Repository rows in the group, those with `status: ok`, and the rest. |
| `shared_tooling_repos` | Repositories whose `shared_tooling` block is available: the evidence the two counts below rest on. |
| `distinct_ci_sources`, `distinct_marketplaces` | Distinct shared CI sources and agent marketplaces the group's scanned repositories reference (`shared_tooling` ids, git-tracked files only). A marketplace manifest and a settings entry naming it count once. `null` when `shared_tooling_repos` is 0 (every scan failed or degraded, or `--scans` left the block out), never a false 0. |
| `agent_building` | Scanned repositories per agent-building class: `{repos, builds_agent, builds_mcp_server, uses_agent_tooling, none}`, where `repos` counts those with an available `agent_building` block. An inventory, not a level. `null` when no repository's block is available, never a false 0; absent in rows written before it was added. Classes: see [agent clusters](agent-clusters.md#output-agent_clustersjson). |
| `settings_scope` | The settings fragment joined: the one whose `scope` equals the group, else the nearest ancestor (a top-level GitLab group covers its subgroups); `null` when none matches. |
| `settings` | That fragment's settings, each `{value, reason}` or `{available: false, reason}` when ghreport read it as `unknown`. Counts-only (the default) keeps a value that is a short token (`on`, `off`, `full`) and writes any value that is not a short token (a list of rule or ruleset names) as `set`, with an unavailable setting's reason reduced to `unknown` and a value's reason to `null`; `--full` keeps them. `null` without a match. |
| `levels_version` | The maturity model's `version`; `null` without `--levels`. |
| `levels` | One entry per maturity model row: `id`, `scope`, `status` (`level` or `unknown`), `level` (a whole number, `null` when unknown), `top_level` (the row's number of levels, so a level reads as a share of it without the maturity model file; absent in rows written before it was added, which readers handle), `aggregation` (the team rule, `{rule, share}`; `null` for org rows), `missing_signals`, `unknown_repos`; on an `unknown` row only, optional `floor` (the level its confirmed criteria reach, when at least 1: "at least N", never the exact level) and `full_run_only` (the missing signals only a full `/assess` measures, their block unavailable as `not_registered` or `network` in every repository missing them; a `--platform` scan's block such as `review_reality` is left out, since `fleet --platform` measures it, and the next-steps page says so). Both are absent otherwise and in rows written before #697, which readers handle. |

Maturity model signal paths are checked at load against `lib/scoring/level_signals.py` (an
unknown path exits 2 before any scan) and evaluated by `lib/scoring/levels.py`, with no
second threshold config. The shipped `skills/assess/levels/default.toml` uses
the same namespace.

Runs written before 2.0.0 name these fields `rubric_version` and `rubric` (and
`run.json` names its version `rubric_version`). Every reader (`heatmap.py`,
`fleet_next_steps.py`, `--compare` and the run history) maps the old names, so
earlier history entries still load and match. `--compare` judges such a run by the
stamped versions, never by the plugin major version (#867). The old `--rubric` flag exits 2 naming
`--levels`.

- `team` rows read each repository's signal blocks by block name, as in a
  run-context (`nested_instructions.budget.claude_code.lines`). A failed
  repository joins with no signals, so its level is unknown: a `min` row never
  reads better than the evidence.
- `org` rows read the joined ghreport fragment: `scope`, `provider` and
  `<setting>.value` / `<setting>.reason`, for example `two_factor.value` `eq "on"`.
  A setting ghreport reported `unknown`, or a group with no settings, makes the
  row `unknown`, never a false 0.

Group rows contain no timing or per-run stamp, so a rerun over unchanged
inputs on the same day is byte-identical.
