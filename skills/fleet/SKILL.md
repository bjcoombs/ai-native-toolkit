---
name: fleet
description: "Run the headless /assess signals across every repository under a ghsync root (or in a list file) and write one counts-only JSON line per repository, deterministically and without writing to any target. Each repository runs assess_signals.py in its own process from a worker pool; one that cannot be scanned is a failed row with a reason token and never stops the run. No model in the gathering path; an optional post-gather step (--label) has a small model label each repository (service, library, infrastructure, mirror, docs, other) into a separate labels.jsonl for display and filtering only. Each counts-only run is kept in a dated run history and can be compared with an earlier run (level movement apart from measured-set movement). TRIGGER when the user types /fleet, wants to measure a team, group or whole estate rather than one repository, asks to run /assess signals across all repos in a ghsync checkout or a list of repos, wants per-repository signal JSONL for a fleet roll-up, wants the repositories in a fleet run labelled or grouped by kind, or asks what moved since the last fleet run or sprint."
---

# fleet: headless signals across a ghsync root

`/assess` measures one repository; `ghsync` mirrors an estate. `fleet` joins
them: it runs the headless signals entry point (`assess_signals.py`) on every
repository and writes one JSON line per repository plus a run stamp, the same
way every time, so two people measuring the same estate get the same numbers.

Gathering is a pure script: no subagent, no model, and no network call unless you
pass the opt-in [`--platform`](#platform-pass---platform). The only
model step is the optional labelling below, which runs after gathering and
writes its own file.

## How to run it

<!-- chat-skip:start -->
The script lives next to this file; Claude Code writes this skill's directory
into the path below when it loads the skill. It needs the `assess` skill beside
it (the plugin ships both) and Python 3.11+:

```bash
# every repository under a ghsync root
uv run "${CLAUDE_SKILL_DIR}/scripts/fleet.py" ~/dev/gitlab.example.com --out fleet-out

# the repositories in a list file
uv run "${CLAUDE_SKILL_DIR}/scripts/fleet.py" --from-list repos.txt --out fleet-out
```
<!-- chat-skip:end -->

`uv run fleet.py ...` installs the script's three dependencies (networkx,
grimp, lizard), which the per-repository scans need for the default maturity
model's layer 2 and 4 blocks (the import graph and file size); the layer 3
block (lint suppressions) needs ripgrep (`rg`) instead. lizard scores every
tracked source file, so a very large repository can need a longer `--timeout`. `python3 fleet.py ...` without them still runs; those blocks then
read unavailable with a named reason (`no_grimp`, `no_lizard`, `no_rg`) and
their rows `unknown`, and the run prints the install line for whatever is missing.
A repository with no import graph names its dominant
language instead (for example `no_import_graph_rust`), so the
fleet output says which language is missing; `no_python_packages` is a Python
repository with no importable package and `no_source_files` one with no source.

**Without uv.** Install the three dependencies into the interpreter you run
the script with, then run it with that interpreter:

```bash
python3 -m pip install --user networkx grimp lizard==1.23.0
```

**Finding `rg`.** The scans use `RG_PATH` when it names the ripgrep binary,
else `rg` on `PATH`, else (on Windows) the last `rg.exe` in sorted order under
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\BurntSushi.ripgrep*`, where
`winget install --scope user BurntSushi.ripgrep.MSVC` puts it even when no
`PATH` alias is created. A `no_rg` reason names the lookups it tried.

The observability rung, the doc graph, doc staleness and the archetype are
read from committed files and git history, so a fleet run measures them
(Layer 1 and the doc-freshness row get a level). Some blocks are measured
only by a full `/assess` run of one repository and always read unavailable
here, with the reason code `not_registered`: `dead_code`, `runtime` and
`understanding` (the costly scans, opt-in to a full run), plus the blocks a full
run assembles from its own state (`diff`, `behaviour`, `documentation`,
`derived_findings`, `coverage_report`, `test_pressure`, `test_focus`,
`accretion_ratchet`, `gap_actions`, `ancestor_instruction_files`). Blocks that
read GitHub or GitLab (`config_drift`, `review_reality`, `gate_cost_estimate`) read
unavailable with the code `network`: a fleet run makes no network call without
`--platform`. Layer 7 level 1 reads `review_evidence`, which an AI review step
committed to CI (`review_automation`) sets offline, so such a repository reads
"at least 1" on Layer 7 without `--platform`. Doc
staleness reads unavailable (`not_git_root`) outside a git work tree, and its
stale share is null on a shallow clone, so the doc-freshness row is `unknown`
there. The guardrails-stated row reads `guardrails.stated_pct`, the share of the
gates a repository configures (complexity limit, coverage floor, pre-commit
hooks, mypy strict) that an instruction file states; with no gate found it is
`unknown`. The agent-consistency row reads `agent_consistency.conflicts` (level 1:
no check command or threshold one agent system's instruction file names and
another's does not) and `agent_consistency.unlinked` (level 2: every system's
file is `AGENTS.md`, a symlink, an import or a pointer to it); with fewer than
two agent systems it is `unknown`.

| Flag | Meaning |
|------|---------|
| `ROOT` | A ghsync root: `<root>/<path>/<repo>-main` with a sibling `worktree/`, where `<path>` is the repo name (GitHub) or the `path_with_namespace` at any depth (GitLab). |
| `--from-list FILE` | Scan the repositories listed in FILE instead of discovering them. One entry per line: a `<repo>-main` checkout, its repository directory, a plain git checkout, or (with `ROOT`) a path relative to the root such as `ghsync --porcelain` output. Blank lines and `#` comments are ignored. |
| `--out DIR` | Output directory (required). The run writes `DIR/repos.jsonl`, `DIR/groups.jsonl`, `DIR/agent_clusters.json`, `DIR/run.json`, `DIR/fleet-data.json`, `DIR/achievements.json`, with `--levels` `DIR/level.json`, and the diagrams (`DIR/report.md`); a `DIR` inside any target is refused. |
| `--levels FILE` | A maturity model in the `lib/levels.py` TOML format (an invalid maturity model exits 2); its `version` is stamped in `run.json`. Each group row carries every maturity model row's whole level, or `unknown`, and the aggregation rule used. Signal paths: see [group roll-up](#output-groupsjsonl). |
| `--settings PATH` | Group settings from `ghreport`: a JSON file holding one settings fragment or an array of them, or a directory of fragment files (a `ghreport --render-dir`, whose `settings/` subdirectory is read). Each fragment is `{scope, provider, two_factor, push_rules, default_branch_protection, rulesets}` with every setting `{value, reason}`. A GitHub fragment may also carry an optional `teams` list, `[{slug, parent, repos}]` (`parent` a team slug or null, `repos` repository names), which places repositories in the [units tree](#output-fleet-datajson); `ghreport` writes it on a GitHub org fragment. Unreadable input exits 2. |
| `--units FILE` | Org units mapping (TOML): declared units and repository placements for the ownership and system views of [`fleet-data.json`](#output-fleet-datajson). It always overrides the inferred tree. An invalid file, or one naming a parent or unit that does not exist, exits 2 before any scan. |
| `--fleet-config FILE` | Fleet settings (TOML) passed to every repository scan. Validated before any scan: an unknown section or key, or a value of the wrong kind, exits 2 and names the key. See [Fleet settings](#fleet-settings---fleet-config). |
| `--platform` | After the offline scan, run a separate platform pass that reads GitHub or GitLab through `gh` or `glab` and joins its blocks into each `ok` row. Off by default: without it no network call is made. See [Platform pass](#platform-pass---platform). |
| `--platform-jobs N` | Repositories read in parallel by the platform pass (default: `[platform] jobs`, else 4). Needs `--platform`. |
| `--platform-timeout S` | Seconds one repository's platform read may take before its platform blocks are unavailable (default: `[platform] timeout`, else 300). Needs `--platform`. |
| `--jobs N` | Repositories scanned in parallel (default: CPU count). |
| `--window W` | History window passed to `assess_signals.py` (`6mo`, `90d`, `12w`, `1y`). |
| `--scans a,b` | Only these scans (default: all), plus what they read. `review_reality` also brings `review_automation` and `review_evidence`, the Layer 7 level 1 signal. |
| `--timeout S` | Seconds one repository may take before its row is marked failed (default 600). |
| `--full` | Keep the full signal blocks, free text included. Default is counts-only. |
| `--hash-paths` | Counts-only paths hashed under one salt drawn for this run: hashes join within the run, but reruns are not byte-identical. |
| `--hash-salt VALUE` | Hash paths under this salt; reruns stay byte-identical and join across runs. |
| `--delete-after` | Delete each clone once it is scanned, to keep disk use flat over a large estate. With `--platform` no clone is deleted until the whole offline scan is done, so peak disk use is the whole estate. Narrow and fail-closed; see [`--delete-after`](#--delete-after). |
| `--compare [RUN_DIR]` | Compare this run with an earlier one and write `DIR/compare.json`. `RUN_DIR` is an `--out` directory or a history entry; a bare `--compare` takes the latest history entry whose stamp matches this run. See [Run history and comparison](#run-history-and-comparison). |
| `--history-dir DIR` | Run history root (default `${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet`). |
| `--history-org NAME` | The `<org>` directory in the history (default: from the repository ids). |
| `--no-history` | Do not keep this run in the history. |
| `--discovered FILE` | What ghsync discovered: its `--porcelain` output (one path per line) or `--porcelain-json` output (one object per line). `report.md` and `run.json` then say how many of these repositories were assessed and count each gap. Needs `ROOT`; a malformed line exits 2 and names the line. See [Coverage](#coverage---discovered). |
| `--name-agent-clusters` | Name the member repositories and groups of each cluster in `DIR/agent_clusters.json`. Off by default, in counts-only and `--full` alike: a cluster then carries counts and SDK slugs only. See [agent clusters](#output-agent_clustersjson). |
| `--targets FILE` | Targets (TOML) for [`achievements.json`](#output-achievementsjson): each `[[target]]` names a `view`, a `unit`, a team `row` and either a `level` or a mean `share` of top level (see the targets file format below). Needs `--levels`. An invalid file exits 2 before any scan. Not part of the run stamp, so changing a target never stops runs comparing. |
| `--first-achievements` | Also credit `first` achievements in [`achievements.json`](#output-achievementsjson) (a unit reaching a level no unit of its view and kind has held on that row in this fleet's history). Off by default (#772), since it compares a unit with its peers. Not part of the run stamp. |
| `--no-diagrams` | Do not draw the [diagrams](#output-reportmd-and-diagrams) (`DIR/report.md` and its SVGs); any a previous run left in `DIR` are removed. |

**Exit codes.** `0`: the run completed and `repos.jsonl` and `run.json` were written, including
when some repositories failed (each is a `failed` row; stderr prints the count
and the failed ids). `2`: usage error - no `ROOT` and no `--from-list`, a root
or list that does not exist, no repositories found, `--out` inside a target, an
invalid `--levels`, `--settings`, `--fleet-config` or `--units` (a settings directory with no `*.json` fragment
file included, or a units file naming a missing unit), a list entry whose `~user` cannot be expanded, an unknown scan, a bad
window, a `DIR` that cannot be created or written to (checked before any scan, so
`--delete-after` never removes a clone whose results could not be written), an
unusable run history location, or a `--compare` directory that is missing or
malformed. A window reaching back before year 1 is accepted: every dated `HEAD` is
inside it.

### Windows

Python 3.11+ and Git for Windows are enough; uv and admin rights are not
needed. Installs, PATH notes and the make-free commands are in the
[Windows setup guide](../../docs/windows.md).

<!-- chat-skip:start -->
A Claude Code session on Windows runs PowerShell, so use `python` (not
`python3`) and these forms:

```powershell
# dependencies, once, without uv
python -m pip install --user networkx grimp lizard==1.23.0

# only if rg is neither on PATH nor installed by winget
$env:RG_PATH = "C:\tools\ripgrep\rg.exe"

# every repository under a ghsync root
python "${CLAUDE_SKILL_DIR}/scripts/fleet.py" "$HOME\dev\gitlab.example.com" --out fleet-out

# the repositories in a list file
python "${CLAUDE_SKILL_DIR}/scripts/fleet.py" --from-list repos.txt --out fleet-out
```
<!-- chat-skip:end -->

`ghsync` and `ghreport` are bash scripts and need Git Bash on Windows. Without
it, clone the repositories any other way, write one checkout path per line to a
list file, and pass it with `--from-list`.

## Output: `repos.jsonl`

One line per repository, sorted by `repo`, keys sorted. Schema:
[`references/repos.schema.json`](references/repos.schema.json).

| Key | Value |
|-----|-------|
| `fleet_row_version` | Row envelope version (2; 2 added `active`). |
| `repo` | The repository id: its path under the root, prefixed with the root's own name when that path is one segment (a GitHub org root gives `org/repo`). A listed entry outside `ROOT` (or with no `ROOT`) takes its `origin` remote's path, else its last two directory names. The same checkout gets the same id from a root run or a list run. |
| `group` | `repo` without its last segment (`grpa/svc1` is in `grpa`). |
| `status` | `ok` or `failed`. |
| `reason` | `null` when ok; a token when failed: `missing_checkout`, `not_a_git_work_tree`, `scan_timeout`, `invalid_output`, `scan_error`, or `scan_exit_<code>`. |
| `signals_schema_version` | The `assess_signals` document version; `null` when failed. |
| `active` | `true` when the committer date of the checkout's `HEAD` falls inside the window (`--window`, default `6mo`, counting back from the run's UTC date in whole days, months and years by calendar), read locally from git; `null` when failed. Close to `ghsync --active-since` but not identical: ghsync counts 30-day months from the current time on platform dates, so cutoffs can differ by a few days. |
| `scans` | The `assess_signals.py` `scans` map (counts-only unless `--full`); the block shapes are `skills/assess/references/signals.schema.json` and `skills/assess/references/counts-only.md`. `null` when failed. `scans.review_automation.present` is the row's "AI review in CI: yes / no" (#696): an AI review step or review app config in a git-tracked file, with `kinds` saying which (`ci_action`, `ci_component`, `config_file`). A GitHub review action counts only when its workflow runs on pull requests (`pull_request`, `pull_request_target`, `merge_group`); one wired only to `issue_comment` or `issues` is listed with `on_changes: false`. A GitLab include counts as committed: whether its jobs run in merge-request pipelines is not read. Includes inside a template resolved from the root (see GitLab includes below) are matched too, each such match carrying the `depth` it was found at. |

**`logs/<repo>.stderr`.** When a scan process exits non-zero, the last 4000
characters of its stderr go to `DIR/logs/<repo>.stderr` (the id's segments as
directories) for diagnosis. The row keeps only its `scan_exit_<code>` token. A
log is written only for such a failure; each run first removes the `*.stderr`
files a previous run left under `DIR/logs/` (nothing else there is touched).

Rows carry no absolute path, timing, error text or per-repository stamp, so
two runs over unchanged checkouts on the same day are byte-identical (except
with `--hash-paths` and no salt). Repository ids are kept in clear text even
with hashing on.

## Output: `doc_freshness.jsonl` (opt-in)

Written only when a scanned repository's `.assess/config.toml` declares
`[[describes]]` mappings (a central docs repository naming the service surface
each doc describes; see the assess skill's config section). One row per mapping,
method `cross-repo-explicit`: the service commits to the mapping's `scope`
authored after the doc's last content change, skipping merges, Dependabot,
Renovate and other `[bot]` accounts, lockfile or manifest-only commits, and bulk
mechanical commits (the content clock's rule). `status` is `behind` at 3 or more
such commits (a starting threshold awaiting tuning on a real fleet),
`minor_drift` at 1-2, `current` at 0; only `behind` is a finding. A doc or scope
path that no longer exists is `broken`; a repository absent from the run, a
shallow checkout whose history ends after the doc's last edit, or a git failure
is `unknown` with a `reason` token. Rejected entries (a missing `scope`, say) are
`invalid` rows and the message is printed to stderr. Counts-only rows carry the
count and the doc's last-change date only; `--full` adds up to five commit shas and subjects so a
human can check the verdict. `--hash-paths` hashes the doc and scope paths. The
join runs before any scan (so `--delete-after` cannot remove a checkout it
needs), costs one `git log` per mapping plus one content-clock pass per docs
repository, makes no model call, and is kept in the run history beside the
other files. Schema: [`references/doc_freshness.schema.json`](references/doc_freshness.schema.json).
Logic: `skills/assess/scripts/lib/fleet_doc_freshness.py`.

## Output: `groups.jsonl`

One line per group (a repository row's `group`), sorted by group, keys sorted.
Schema: [`references/groups.schema.json`](references/groups.schema.json).
Logic: `skills/assess/scripts/lib/fleet_groups.py`.

| Key | Value |
|-----|-------|
| `fleet_group_version` | Row envelope version (1). |
| `group` | The group id. |
| `repos`, `scanned`, `failed` | Repository rows in the group, those with `status: ok`, and the rest. |
| `shared_tooling_repos` | Repositories whose `shared_tooling` block is available: the evidence the two counts below rest on. |
| `distinct_ci_sources`, `distinct_marketplaces` | Distinct shared CI sources and agent marketplaces the group's scanned repositories reference (`shared_tooling` ids, git-tracked files only). A marketplace manifest and a settings entry naming it count once. `null` when `shared_tooling_repos` is 0 (every scan failed or degraded, or `--scans` left the block out), never a false 0. |
| `agent_building` | Scanned repositories per agent-building class: `{repos, builds_agent, builds_mcp_server, uses_agent_tooling, none}`, where `repos` counts those with an available `agent_building` block. An inventory, not a level. `null` when no repository's block is available, never a false 0; absent in rows written before it was added. Classes: see [agent clusters](#output-agent_clustersjson). |
| `settings_scope` | The settings fragment joined: the one whose `scope` equals the group, else the nearest ancestor (a top-level GitLab group covers its subgroups); `null` when none matches. |
| `settings` | That fragment's settings, each `{value, reason}` or `{available: false, reason}` when ghreport read it as `unknown`. Counts-only (the default) keeps a value that is a short token (`on`, `off`, `full`) and writes any value that is not a short token (a list of rule or ruleset names) as `set`, with an unavailable setting's reason reduced to `unknown` and a value's reason to `null`; `--full` keeps them. `null` without a match. |
| `levels_version` | The maturity model's `version`; `null` without `--levels`. |
| `levels` | One entry per maturity model row: `id`, `scope`, `status` (`level` or `unknown`), `level` (a whole number, `null` when unknown), `top_level` (the row's number of levels, so a level reads as a share of it without the maturity model file; absent in rows written before it was added, which readers handle), `aggregation` (the team rule, `{rule, share}`; `null` for org rows), `missing_signals`, `unknown_repos`; on an `unknown` row only, optional `floor` (the level its confirmed criteria reach, when at least 1: "at least N", never the exact level) and `full_run_only` (the missing signals only a full `/assess` measures, their block unavailable as `not_registered` or `network` in every repository missing them; a `--platform` scan's block such as `review_reality` is left out, since `fleet --platform` measures it, and the next-steps page says so). Both are absent otherwise and in rows written before #697, which readers handle. |

Maturity model signal paths are checked at load against `lib/level_signals.py` (an
unknown path exits 2 before any scan) and evaluated by `lib/levels.py`, with no
second threshold config. The shipped `skills/assess/levels/default.toml` uses
the same namespace.

Runs written before 2.0.0 name these fields `rubric_version` and `rubric` (and
`run.json` names its version `rubric_version`). Every reader (`heatmap.py`,
`fleet_next_steps.py`, `--compare` and the run history) maps the old names, so
earlier history entries still load and match. `--compare` against a 1.x run from
2.x is refused for the plugin major version, as for any major change. The old `--rubric` flag exits 2 naming
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

## Output: `level.json`

Written with `--levels`, beside `groups.jsonl`: each scanned repository's level result,
keyed by its repository id (a failed scan has no entry), in an envelope of
`level_result_version`, `levels_version`, `toolkit_commit` and `plugin_version`. The
result (`scripts/lib/level_tally.py` in the assess skill, #744) treats each row level of
the maturity model as one condition, `<row id>.<level>`, and reports:

- `conditions[]`: `id`, `row`, `level`, `grade`, `counted`, `state` (`met`, `unmet`,
  `not_measured`; `not_applicable` and `declined` are reserved) and a `reason` code when
  not measured (`needs_full_run`, `needs_platform`, a block's own code, `block_absent`,
  `no_value`, `type_mismatch`, `degenerate_history`, or `requires:<id>`);
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

## Output: `fleet-data.json`

One JSON document per run beside `groups.jsonl`: the versioned bundle the fleet
artifact reads (#692). Keys sorted, compact, every list sorted; the same inputs give the
same bytes. Schema: [`references/fleet-data.schema.json`](references/fleet-data.schema.json).
Logic: `lib/fleet_units.py`, `lib/fleet_repo_facts.py`, `lib/fleet_data.py`. It is kept in
the run history beside the other files.

| Key | Value |
|-----|-------|
| `fleet_data_version` | Document version (1). |
| `run` | The run stamp's fields from `run.json`, copied one by one: every field but `fleet_stamp_version` and `fleet_config_entries` (`platform` reads `false` for a stamp written before it). A new stamp field fails a test until it is copied or excluded here, with a `fleet_data_version` bump. |
| `model` | Each maturity model row: `id`, `title`, `scope`, `top_level`. |
| `views` | One entry per view, `ownership` and `system`, each with its `units`. |
| `repos` | One entry per repository row, sorted by `key`. |

**Units.** A unit is an org, division, department, team or system. Each has a `key`
(`<view>/<id>`), `view`, `parent` (the parent's key, `null` at a root), `kind`, `source`
(`path`, `github_teams`, `codeowners` or `mapping`), `name`, `owners` and `owner_count`,
`repos` (repositories below it at any depth), and its roll-ups. The ownership view is
built from, in order:

1. the repository path: GitLab nested groups (`grp/sub/proj` sits in `grp/sub`, under `grp`);
2. GitHub teams from a `--settings` fragment's `teams` list (a repository in several teams takes the deepest, then the first slug);
3. CODEOWNERS, as a fallback for a repository in a one-segment group no team placed: a
   catch-all (`*`) owner that is a team under its group (`@acme/platform`, matched
   ignoring case, so `@Acme/Platform` too) places it in that team;
4. the `--units` mapping, which always wins.

An inferred unit's kind follows its place: a root is `org`, a unit that holds a
repository is `team`, any other is `division` one level down and `department` deeper.
An inferred unit the mapping left with no repository is dropped. The system view comes
from the mapping alone. A repository sits in at most one unit per view, so it is counted
once per view.

The mapping file:

```toml
[[unit]]
view = "ownership"        # or "system"; default "ownership"
id = "acme/platform"      # unique within its view
kind = "division"         # org, division, department, team, system
parent = "acme"           # optional, a unit of the same view
name = "Platform"         # optional
owners = ["@acme/leads"]  # optional

[[repo]]
repo = "acme/svc1"        # a repos.jsonl id
ownership = "acme/platform"
system = "checkout"
owners = ["@acme/payments"]  # optional; replaces the CODEOWNERS owners
```

A mapped repository not in the run is skipped and named on stderr.

**Roll-ups.** Per unit, one entry in `levels` per `team` row: `measured` of `repos`
("8 of 12 measured": an unknown or failed repository is counted in `repos`, never read
as 0) and `lowest_level`, the lowest level a measured repository holds on the row
(`null` when none was measured). Steps (#744) are not yet available, so the lowest row
level stands in for the lowest Step. The roll-up holds no average and names no
repository (#772: the no-comparison rule of #740), so `fleet-data.json` version 2 has no
`mean_share`, `weakest` or `overall`. `org` rows read a group's settings, not a
repository, so they have no unit roll-up; their levels are in `groups.jsonl`.

**Repositories.** Each has `key` (its id), `status`, `reason`, `active`, `web_url` (the
`origin` remote as `https://<host>/<path>`, credentials, an ssh port and `.git` dropped; `null`
for a local remote), `owners` and `owner_count` (the mapping's owners, else the
CODEOWNERS catch-all owners), `units` (`{view, unit}` per view it sits in), and `rows`,
one cell per `team` row: `status`, `level`, `top_level` and the criterion behind it. Below the top level, `signal` and `value` are the first unmet criterion of the
next level and its measured value; at the top level both are `null`; an unknown cell
names the signal that could not be read and `reason` (`unavailable`, `type_mismatch`, or
`not_scanned` for a failed repository).

**Privacy.** `--full` keeps everything. Counts-only (the default) keeps keys, which carry
the group and team paths (`ownership/acme/platform`) and repository ids unless
`--hash-paths` or `--hash-salt` is given, and tokens and
web URLs (so the host name of each remote appears, in `--out` and the run history), writes a string `value` that is not a short token as `set`, and drops unit
names and owners (`owner_count` stays). With `--hash-paths` or `--hash-salt`, every unit
and repository key, and every reference to one, is hashed (`h:` plus 16 hex digits) and no
name, owner or web URL is written: the file names no repository, group or host. Hashes
join across runs only under one `--hash-salt`. The rules are `FLEET_DATA_RULES` in
`lib/counts_only_rules.py`. This path hashing applies to `fleet-data.json` only
(`achievements.json`, built from it, carries the same hashed keys): `repos.jsonl` keeps
repository ids in clear, so under hashing a `fleet-data.json` repository cannot be joined
to its `repos.jsonl` row. GitHub team slugs from a `ghreport` fragment's `teams` list
appear only as unit keys, so they are hashed with them.

## Output: `achievements.json`

What each unit and repository earned, and the next step for each (#704, parent #692).
Written on every run beside `fleet-data.json`, from run data only: the engine replays
this run and the earlier comparable runs' `fleet-data.json` copies in the run history,
oldest first, every time. It never reads an earlier `achievements.json`, so editing
output cannot change what is earned. Keys sorted, compact; the same history gives the
same bytes. Schema: [`references/achievements.schema.json`](references/achievements.schema.json).
Logic: `lib/fleet_achievements.py` (engine), `lib/fleet_achievements_runs.py` (history),
`lib/fleet_targets.py` (`--targets`), `lib/fleet_credit.py` (report section),
`lib/fleet_rescore.py` (re-score and Recount).

**Comparable runs.** A history entry is replayed when its `run.json` passes the
`--compare` rule against this run (plugin major version, maturity-model version, window,
output mode, scans, path hashing, `--fleet-config`, `--platform`) and it holds a
`fleet-data.json` at version 1 or 2 (version 1 entries carry the old roll-up, which the
engine does not read). One run per date, dates before this run's. Without
the history (`--no-history`, `--full`) only this run is read: no achievement, just next
steps, with `note` saying why.

**A maturity-model change re-scores the history (#771).** An entry whose only difference
from this run is the maturity-model version (a `--levels` file with a new `version`) is
not dropped: the engine re-scores it, evaluating the scans in its `repos.jsonl` under
this run's model in memory (history files are never rewritten), and replays it as if it
were comparable, so streaks and level-ups survive a version bump. An entry is still passed
over when its `repos.jsonl` holds no readable scans or a stored repository key does not
join under this run's key (another `--hash-salt`). A row whose new criterion reads a
signal the stored scans lack re-scores as unknown in the earlier runs: neutral, never a
slip. Pure signal renames are read under their new name (`SIGNAL_ALIASES` in
`lib/fleet_rescore.py`, empty until a rename ships), so a rename causes no Recount line.
`--compare`, `trend.svg` and the heat map's compare view still refuse across a
maturity-model change.

**Subjects.** Every unit of every view (`view` `ownership` or `system`) and every
repository (`view` and `unit_kind` `repo`). A unit's level on a team row is the row's
roll-up rule (`min` or `share`) over its measured repositories, those with a known level.

**Unknown is neutral, with one exception.** A row counts only when it *moves cleanly*: known in
both runs over the same measured set of repositories. A repository going unknown,
failing its scan, joining or leaving a unit, or hashed under another salt makes that
move neutral. One exception, on a step from a re-scored entry into a run at another
maturity-model version (a step between two runs re-scored from the same version keeps the
credit it had under that version): a repository's own move
from a known level to unknown breaks its streak, and from unknown to a level above 0 extends
it; a cell the re-score itself made unknown stays neutral. Under counts-only, credit goes to units and repositories, never to people:
owners are never read.

**Kinds.** The oldest replayed run is the baseline and earns nothing. `first` is off by
default (#772): it compares a unit with its peers, so it is earned only with
`--first-achievements`. There is no personal-best kind.

| Kind | Earned when |
|------|-------------|
| `level_up` | A row moves cleanly to a higher level. |
| `first` | Off by default; on with `--first-achievements`. A `level_up` to a level no subject of the same view and kind has held on that row in this fleet's history. Every subject reaching it in the same run is credited: a milestone, never a ranking. A view and kind with one subject (the org root) earns none. |
| `target_hit` | A row moves cleanly from below a `--targets` target to at or above it: a `level` target reads the row's level; a `share` target reads the unit's mean share of top level on the row over its measured repositories (worked out by the engine from the repository cells), so it can be hit while a `min` row's level stays put. |
| `streak` | Runs since the subject last slipped (a clean move down on any row) reach 3, 5, 10, 25, 50 or 100. A run with no clean move neither breaks nor extends it. |

Each achievement has `kind`, `view`, `unit` (the key `fleet-data.json` writes),
`unit_kind`, `run` and `previous_run` (run ids are scan dates), `evidence` (`row`,
`from_level`, `to_level`, `level`, `share_from`, `share_to`, `target_share`, `runs`, and `moves`: each
repository that moved up, from which level to which, and `criterion`, the next-level
criterion that went from unmet to met) and `credit`, one Markdown line.

**Next steps.** `nearest_level_ups` holds one entry per subject and team row below the
top level: among the measured repositories one level below the next, the next-level
criterion most of them share (`signal`), those `repos`, how many more must reach the next
level for the subject to move (`needed`), and the `change` and `done_when` in the team
next-steps pages' wording. `targets` gives each target's current `level` and `share`
(the row's mean share of top level), and its distance: `distance` in levels for a level
target, `share_distance` in share for a share target (0 when met; null when the row is not
measured this run or the target is of the other kind). `target` holds a level target's
level and `share_target` a share target's share; the other is null.

**Recount.** `recount` is set on the first run under a new maturity-model version,
when the newest replayed earlier entry was re-scored, and null otherwise (on the next run
that entry is at the current version, so no stored state is needed). It holds
`from_version`, `to_version`, `run` (the re-scored entry) and `lines`: one per repository
and team row whose stored cell (its scans under its own rules) differs from its re-scored
cell (the same scans under this run's rules). Every line is attributed to the rules
(`attributed_to` `rules`) and says the repository did not change; its `direction` is
`down` (cause "Now checked", the new criterion as `signal`), `up` ("No longer checked",
the old criterion), `now_measured` (unknown to a level, "Now measured") or `not_measured`
(a level to unknown, "Not measured under the new rules"). Each line has `repo`, `row`,
`from_status`, `from_level`, `to_status`, `to_level`, `cause`, `signal` and `text`, one
Markdown line:
`` `<repo>` <row title>: level <A> -> <B> under maturity model <new> (was <old>). <Cause>: `<signal>`. The repository did not change. ``
Recount lines are display-only: they never earn an achievement, break a streak or change
a level reached or a best share. `rescored_runs` lists every replayed run that was
re-scored from an earlier maturity-model version (empty when none was); the report's
opening line states the unknown exception whenever it is non-empty, Recount or not.

**Targets file** (`--targets`):

```toml
[[target]]
view = "ownership"      # "ownership" (default), "system", or "repo"
unit = "acme/payments"  # a unit id in that view, or a repository id for "repo"
row = "layer-5-ci"      # a team row of the maturity model
level = 2               # 1 to the row's top level

[[target]]
unit = "acme"           # a mean-share target: the unit's mean share of top level on the row
row = "layer-5-ci"
share = 0.5             # above 0 and at most 1
```

Each target sets exactly one of `level` and `share`. A share target is hit on the run the
unit's mean share on the row moves cleanly from below `share` to at or above it, and its
distance is reported in share. Credit names units and repositories only, never people.
With hashed paths the unit is hashed under the run's salt before it is matched.

**Report.** `report.md` opens with "What changed and who earned it", before the
diagrams: this run's achievements by kind (up to 20 lines each), then each unit's nearest
level-up (the row needing the fewest repositories), then target distances. On the first
run under a new maturity-model version a `### Recount` subsection follows "Earned this
run": the Recount lines, raised moves first (up to 20). Every line is
credit or a next step. With `--no-diagrams` there is no report; `achievements.json` is
still written.

## Output: `agent_clusters.json`

Which teams are building the same agent? Written on every run from the
`agent_building` blocks in `repos.jsonl` (an unscored inventory, issue #689).
Logic: `skills/assess/scripts/lib/fleet_agent_building.py`; per-repository scan:
`skills/assess/scripts/lib/agent_building.py`. Not kept in the run history.

Each repository gets one class, first match wins:

1. `builds-agent`: an agent framework is declared in a dependency manifest
   (LangChain, LangGraph, the Claude Agent SDK, OpenAI Agents, CrewAI ...), or a
   model SDK is declared (Anthropic, OpenAI, Gemini ...) and either a source file
   matches a tool-call loop or the repository has the API-client pairing.
2. `builds-mcp-server`: a source file registers MCP server tools, resources or prompts.
3. `uses-agent-tooling`: agent configuration only: an agent hint file (`CLAUDE.md`,
   `AGENTS.md` ...), `.mcp.json`, `.claude/agents/`, or a CI job that runs an agent.
4. `none`: otherwise, including a model SDK with no tool-call loop and no pairing.

The pairing is an issue-tracker API client (Jira, Linear) and a Git-host API
client (GitHub, GitLab) in the same repository, from manifests or REST endpoints
in source: the marker of a ticket-to-merge-request agent. A Git host counts as its
own issue tracker when the source calls both its issues API and its merge-request
or pull-request API through that host's client (python-gitlab, PyGithub, Octokit,
go-gitlab and the like), so an agent that reads GitLab issues and opens merge
requests on GitLab has the pairing too; issues on one host and pull requests on
another do not. Test files, vendored
trees and untracked files are not evidence. SDKs, config kinds, trackers and
hosts are slugs from a pinned vocabulary, so counts-only keeps them.

Cluster rule: two `builds-agent` repositories are linked when they have the same
class, the Jaccard similarity of their SDK and framework sets is at least 0.5
(two empty sets are not similar), and they have the same pairing value (both
carry the issue-tracker plus Git-host marker, or neither does). Clusters are the
connected components of that relation; a single repository is not a cluster.
Deterministic and offline.

| Key | Value |
|-----|-------|
| `agent_clusters_version` | File version (1). |
| `rule` | The cluster rule in words. |
| `named` | `true` with `--name-agent-clusters`. |
| `evidence_repos` | Scanned repositories with an available `agent_building` block. |
| `builds_agent_repos`, `clustered_repos` | `builds-agent` repositories, and how many of them are in a cluster. |
| `clusters` | Largest first, then by SDK union and pairing (never by name): `id` (`c1`, `c2` ...), `agent_class`, `size`, `groups` (distinct groups spanned), `sdks` (union), `shared_sdks` (intersection), `pairing`. Only with `--name-agent-clusters`: `repo_ids` and `group_ids`. |

Without the naming flag no repository id or group name appears in the file, so a
cluster says that two teams build similar agents without saying which.

## Output: `run.json`

One JSON object per run beside `repos.jsonl`, keys sorted, saying what produced
it. Schema: [`references/run.schema.json`](references/run.schema.json).

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
| `repositories` | `selected` (every row), `scanned_ok` (rows `ok`), `failed` (rows `failed`) and `qualifying` (rows `ok` with `active` true). |
| `coverage` | The [`--discovered`](#coverage---discovered) counts: `discoverable`, `assessed`, `archived_or_empty`, `not_cloned`, `failed_scan`, `excluded_by_filters` and `not_in_listing`. `null` without `--discovered` (coverage unknown), and absent in a run from before #745. Counts only, and not a comparability key. |

The stamp holds no path, salt, timing or input mode: a root run, a list run and
any `--jobs` over unchanged checkouts on the same day write the same bytes. A list
run that leaves a checkout out differs in `repositories` (`selected`, `scanned_ok`) and,
with `--discovered`, in `coverage` (`excluded_by_filters`).

## Fleet settings: `--fleet-config`

One TOML file of fleet-wide lists and settings, with one section per consumer:

```toml
[ai_usage]   # agent identities (#695)
[review]     # AI review components in CI (#696)
[platform]   # --platform settings (#714)
[identities] # confirmed bot and agent accounts (#765)
```

The sections are known and may be empty or left out. Each consumer adds its keys;
until it does, any key in a section is unknown and exits 2.

| Key | Meaning |
|-----|---------|
| `[review] ai_review_markers` | Strings an AI reviewer prints in its GitLab merge-request notes (a list; case-insensitive substrings). With `--platform`, a note carrying one counts as AI review in `review_reality.bot_review_share`, on top of the pinned markers (an "AI Code Review" heading, an HTML marker comment) and agent accounts. An access-token user's unmarked note is not review unless `[ai_usage] token_users_as_agents` or an identity rule makes it an agent: coverage, Danger and release bots post through tokens too. |
| `[review] ai_review_components` | GitLab CI component or project paths, and GitHub `owner/repo` actions, that run an AI review in CI, beside the pinned list in `lib/review_automation.py` (#696). Compared lowercased with the host and `@version` dropped; a project path covers the components under it. Read by `review_automation`; a match reports `rule: config` and never the entry itself. |
| `[identities] agents`, `bots`, `people` | Accounts the human confirmed (#765): literal e-mails or logins such as `codecov[bot]`, matched case-insensitively, never patterns. `agents` count as agent authors in `ai_usage`; `bots` and `people` never do, whatever a pinned agent rule says. A dependency bot (Renovate, Dependabot) stays in `dependency_bot_commits` whatever the answer. The fleet lists extend each repository's own `[identities]` (`/assess` SKILL.md and `skills/assess/references/identities.md`). |
| `[platform] jobs` | Default for `--platform-jobs` (a whole number of at least 1); the flag wins. |
| `[platform] timeout` | Default for `--platform-timeout`, in seconds; the flag wins. |

**Confirming accounts across the fleet.** The rows carry only `ai_usage.identities_awaiting_confirmation`, a count. To see the accounts, run the listing over every repository at once; it never prompts and prints to the terminal only, aggregated by account with the commits summed and the repositories counted:

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/../assess/scripts/identity_candidates.py" --from-list repos.txt --fleet-config fleet.toml [--json]
uv run "${CLAUDE_SKILL_DIR}/../assess/scripts/identity_candidates.py" --from-list repos.txt --confirm 'bot=codecov[bot]' --config fleet.toml
```
<!-- chat-skip:end -->

In an interactive session, ask one AskUserQuestion round over the aggregated list (AI agent, automation bot or person, with the evidence shown) and record the answers in the `--fleet-config` file with `--confirm ... --config`. The next run lists only accounts not yet classified. A headless fleet run never asks.

The `[platform]` keys are read from the `--fleet-config` file only: the pass is fleet-wide, so a
repository's own `[platform]` values have no effect.

`[platform] jobs` and `timeout` say how a run goes, not what it measures, so they
are left out of `fleet_config_sha256`: changing them never breaks comparability. The loader and the key
tables are `lib/fleet_config.py` (`SECTION_KEYS`).

- **Validation.** `fleet.py` validates the file before any scan. A TOML error, an
  unknown section or key, or a value of the wrong kind exits 2 with the key named
  (`error: --fleet-config FILE: review.foo: unknown key ...`). The message never
  quotes a value.
- **Same config everywhere.** Every repository scan gets one private copy of the
  validated file (`assess_signals.py --fleet-config`), so an edit during the run
  reaches no scan.
- **Per-repository extension.** A repository's `.assess/config.toml` may carry the
  same sections. They extend the fleet file for that repository and never replace
  it. A list key is the fleet's entries in order, then the repository's entries
  not already present: a repository can add an entry, never remove one, and a
  fleet entry keeps its index everywhere. A scalar the fleet file sets keeps the
  fleet value; a different repository value is ignored with a warning naming the
  key. A scalar the fleet file leaves unset takes the repository's value. A key
  that does not validate in a repository file is skipped with a warning, never a
  failed row. Interactive `/assess` reads the repository's sections alone.
- **Stamp and comparison.** `run.json` records `fleet_config_sha256` and
  `fleet_config_entries`. The hash covers the validated content as sorted-key
  JSON with empty sections left out: comments, whitespace, key order and a new
  empty section do not change it, and list order does. A file with only empty
  sections still differs from no file (`null`).
  Runs with different hashes are not comparable (`fleet config changed ...`), a
  same-day run with a different config gets its own history entry, and the trend
  lines join only runs with the same hash.
- **Counts-only.** No config value reaches `repos.jsonl`, `run.json` or any
  other output: consumers report per-rule counts by position, never by pattern.

### `[ai_usage]`: agent identities

Read by the `ai_usage` scan (`lib/agent_identity.py`, `AuthorMatcher`) to count the
commits an in-house agent authored, beside the pinned agent apps (Claude, the
Copilot coding agent, Devin, Jules, Codex, the Cursor background agent, OpenHands,
Amazon Q Developer).

```toml
[ai_usage]
token_users_as_agents = false   # GitLab access-token users count as agents (default false)

[[ai_usage.agent_identities]]
pattern = "^inhouse-agent"      # a regular expression, case-insensitive, searched
# field = "email"               # optional: "email", "username" or "name"
```

| Key | Kind | Meaning |
|---|---|---|
| `agent_identities` | list of tables | One rule per in-house agent. `pattern` (required) is a Python regular expression; a bad one exits 2 naming the key, never the pattern. Without `field` a rule reads the author e-mail and the username; `field = "name"` opts in to the display name, which no pinned default reads. A commit is credited to the first rule that matches, so `ai_usage.agent_identity_rule_commits[i]` counts rule `i` once per commit. |
| `token_users_as_agents` | boolean | Count every GitLab project or group access-token user (`project_<id>_bot_<hex>`, `group_<id>_bot_<hex>`, their `@noreply.<host>` e-mail) as an agent. Without it they are counted apart in `token_user_commits`; a rule such as `pattern = "^project_42_bot_"` promotes one project's token users only. |

The username is the platform login a noreply e-mail carries (GitHub
`[<id>+]login@users.noreply.<host>`, GitLab `<id>-login@users.noreply.<host>`, a
token user's own address). Dependency bots (Renovate, a plain `renovate-bot` user,
Dependabot) are always `dependency_bot_commits`, whatever a rule says. Per-rule
counts are by position in the merged list, so a fleet rule keeps its index in every
repository; the block never carries a pattern or a matched identity.

## Platform pass: `--platform`

The offline scan never reaches the network. `--platform` adds a second, separate
pass after it: for each repository whose row is `ok`, one more
`assess_signals.py --platform` child reads the platform for the scans in
`lib/signals.py` `PLATFORM_SCANS` (`review_reality`, `delivery`, `review_evidence`,
which the pass rebuilds from the measured `review_reality`, and `review_automation` and
`ci_checks`, which read GitLab's merged CI configuration), narrowed by
`--scans` when given. Each needs `gh` (github.com remotes) or `glab` (GitLab
remotes) logged in.

- **One shared sample.** Both blocks read the same sample: up to 30 changes merged
  inside `--window` (counted back from the scan date), the most recently merged
  first. `review_reality` used to read the 30 most recently *opened* merged
  changes with no window, so its shares move between a pre-#715 run and a later
  one, and a narrow window can sample fewer than 30.
- **`delivery`.** `merged_count`, `time_to_merge_hours_p50` / `_p90` (opened to
  merged), `first_human_comment_hours_p50` / `_p90` and `human_commented_share`
  (the first comment or review by a person other than the author), `ai_review_share`
  (a review or comment by a known AI review app, a pinned agent app, or an
  `[[ai_usage.agent_identities]]` rule), and `approved_share` / `self_merged_share`
  copied from `review_reality`, and `agent_authored_share` (changes authored by a
  pinned agent app or an `[[ai_usage.agent_identities]]` rule, by username; a
  GitLab access-token user counts only under `token_users_as_agents`). Durations
  are hours, percentiles nearest-rank. On GitLab the comment and AI-review
  figures come from each merge request's notes (#716): the first human comment is
  the first non-system note by a person other than the author, and AI review is a
  note with an AI-review marker or by an agent or a pinned AI-review app. A figure
  is null when the notes leave it undecided after 3 pages (300 notes) on any
  sampled merge request, or when the notes read is refused. Counts-only output
  (the default) keeps every `delivery` figure, the same as `--full`.

- **GitLab merged CI configuration (#796).** A GitLab repository whose `.gitlab-ci.yml`
  includes a file the offline scan could not read (a template project not cloned under
  `ROOT`, a `template:` or `remote:` file) gets its merged configuration from GitLab's
  CI lint API: one `glab api --hostname <host> projects/<path>/ci/lint?content_ref=<HEAD>`
  GET per such repository. A repository whose every include resolved offline is not
  asked: the template read from `ROOT` wins. `ci_checks.test_or_lint_runs` then reads
  true when a merged job runs a test or lint check (`basis: merged_config`) and false
  when none does, where offline it was null. It stays null, with `basis:
  merged_config_partial`, when the merged configuration may lack jobs: an include under
  `rules:` (GitLab may merge without it) or a `trigger:` child pipeline (not expanded); `ci_checks.merged_config` says whether the
  read ran (`read`, `not_needed`, `invalid`, `unavailable` with `merged_config_reason`,
  or `not_read` offline). `review_automation` credits a review component the merge
  pulled in. A refused or invalid read leaves the offline reading unchanged. The merged
  YAML is never written to disk, cached or published: only a count and a status token.
- **Joined, never failing a row.** The pass's blocks replace the offline
  `network` placeholders in the row's `scans`, and add `delivery`, which a row
  read without `--platform` does not carry at all (absent, never zeros or a
  placeholder); `--scans delivery` without `--platform` exits 2. A refused read (HTTP 403 from `gh` or `glab`) gives
  `review_reality` and `delivery` `available: false` with code `no_access`. It never changes a row's
  `status` or `reason`: a slow or refusing host cannot turn an offline row into
  `scan_timeout`. A read that runs past `--platform-timeout`, exits non-zero or
  writes no valid document gives each platform block `available: false` with
  `platform_timeout`, `platform_exit_<code>`, `platform_error` or
  `platform_invalid_output`. Only a child that exits non-zero
  (`platform_exit_<code>`) leaves its stderr tail in
  `DIR/logs/<repo>.platform.stderr`; a timeout, an error starting the child or
  invalid output writes no log. A refused read (`no_access`, `not_found`,
  `rate_limited`) is an unavailable block with that reason, never zeros. A row
  whose offline scan failed gets no platform read.
- **Network hygiene.** `gh` and `glab` run with stdin closed and prompts disabled
  (`GH_PROMPT_DISABLED`, `NO_PROMPT`, `GIT_TERMINAL_PROMPT=0`). A rate limit (HTTP 429, or GitHub's 403
  "API rate limit exceeded" / "secondary rate limit") is retried up to 3 times, waiting the `Retry-After` seconds when the CLI prints
  it, else 2, 4 and 8 seconds (at most 60 per wait); still limited, the block is
  `rate_limited`. Plain `gh api` and `glab api` calls do not print response
  headers, so in practice the 2, 4 and 8 second fallback applies. `--platform-jobs` caps the concurrent reads. One response
  cache per run, in a temporary directory removed when the pass ends, answers a
  repeated request (an auth probe, a bot-account lookup) once; it never lands in
  `DIR`, the history or a target. The response cache never holds comment or
  review text on either provider: GitHub pull request reviews and comments, the
  GitLab merged list and GitLab merge-request notes are read in memory only and
  never written to disk. Nor does it hold CI configuration text: GitLab's merged CI
  configuration is read the same way.
- **Stamp.** `run.json` records `platform: true` only when the pass ran a
  platform read (`--platform` with `--scans` selecting no platform scan reads
  nothing and stamps `false`); runs with and without the pass
  are not comparable (`platform pass changed ...`), and each gets its own
  history entry and trend line.
- **With `--delete-after`.** A clone is deleted after its platform read, not
  after its offline scan, so the pass can still read its remote. The platform
  pass starts only once the whole offline scan is done, so no clone is deleted
  before then: peak disk use is the whole estate, not flat. On an estate too
  large to hold at once, run without `--platform`, or in batches with
  `--from-list`.
- **Counts-only.** Every platform field is a count, share, flag or duration.

## GitLab includes

Each scan of a ROOT run (and of a `--from-list` run given ROOT) gets `assess_signals.py --ghsync-root ROOT`. The top-level `include:` of a repository's `.gitlab-ci.yml` is then resolved offline (`skills/assess/scripts/lib/ci_includes.py`, #766): `local:` files of the same checkout, `project: <path>` / `file:` / `ref:` files from the clone `ROOT/<path>/<last segment>-main`, and `component: <host>/<project>/<name>@<version>` from `templates/<name>.yml` (or `templates/<name>/template.yml`) of the clone of `<project>`, each nested include in turn to a bounded depth. A pinned `ref:` is read at that ref when the clone holds it; a pin it does not hold (or a CI-variable ref) is `ref_missing`, never read at another version. No `ref:` reads the clone's HEAD (`ref_basis: head`). `scans.ci_checks` then counts the check lines of the resolved files (`included_check_commands`) and lists one record per include item in `includes` (`kind`, `id`, `status`, `ref_basis`, `depth`; no file name or text), with `resolved_includes` and `unresolved_includes`. `test_or_lint_runs` is true when an own or included line runs a check, false when every include resolved and none does, and null while any include is unresolved: a template that is not cloned (`status: not_cloned`), a `template:` or `remote:` include (`not_offline`), a pin the clone does not hold, a glob, or a depth or file-count limit. `scans.review_automation` matches the include entries of resolved files the same way it matches the repository's own. No `gh` or `glab` call is made. With `--delete-after` no root is passed, since a template clone could be deleted before a repository that includes it is scanned, so those includes stay unknown.

## `--delete-after`

The fleet run did not create the clones, so it deletes only what nothing would
miss. Right after a repository's row is in hand (with `--platform`, after its
platform read), its checkout
(`<repo>/<repo>-main`) is removed when all of these hold, and kept otherwise:

- the row is `ok` (a failed scan keeps its clone for diagnosis and a retry);
- the checkout is in the ghsync layout, not a symlink, and a main clone (a
  `.git` directory at its work-tree root, not a linked worktree);
- `git status --porcelain --ignored` is empty: no change, no untracked or
  ignored file (a local `.env` keeps the clone);
- no stash, no commit on `HEAD` or any ref (branch, tag, note, replace or
  custom ref) that no remote branch holds (a clone with no remote is always
  kept), and no linked worktree;
- it is not, and does not contain, `--out`, the working directory or the toolkit
  itself.

Then the sibling `worktree/` and the repository directory are removed only if
empty. A check that cannot run keeps the clone. stderr prints the deleted and
kept counts and each kept repository with its reason token (`scan_not_ok`,
`not_ghsync_layout`, `symlink`, `not_a_main_clone`, `holds_protected_path`,
`local_changes`, `stash`, `unpushed_commits`, `linked_worktrees`, `git_error`,
`delete_error`). Deletion never changes `repos.jsonl` or `run.json`.

## Run history and comparison

Each counts-only run is also kept, so a programme can see what moved since the
last sprint. The run's `repos.jsonl`, `groups.jsonl` and `run.json` (plus
`fleet-data.json`, and `doc_freshness.jsonl` when the run wrote one) are copied to `<history>/<org>/<scan_date>/`, where `<history>` is `--history-dir` or
`${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet`, and `<org>` is
`--history-org` or the first segment every repository id shares (the distinct
ones sorted and joined by `+` when they differ, e.g. `grpa+grpb`). A `--full`
run is not kept: the history holds only counts-only output, so it can live
outside the scanned repositories.

The history fails closed. Only those files are written (through a temp
file, `run.json` last); nothing else is ever deleted, except that a same-day
rerun that wrote no `doc_freshness.jsonl` removes the replaced entry's copy so
the entry never mixes two runs. `<org>` and the date must each
be one plain directory name, no directory on the way may be a symlink, and the
history may not be inside a target (exit 2 before any scan). These checks run
only when the history is used: a kept run or a bare `--compare`. With
`--no-history` or `--full` (and an explicit `--compare RUN_DIR`) `<org>` is never
resolved, so an id whose first segment is not a safe directory name (`My
Projects/svc`) still runs; otherwise pass `--history-org`. A same-day rerun replaces the day's entry only when that entry is a
fleet run with the same maturity-model version, window, output mode, scans and path
hashing over the same repository set (a partial `--from-list` rerun never
replaces the full-fleet entry). Otherwise the entry is kept and the run goes to a suffixed sibling,
`<date>.2`, `<date>.3` and so on (the first that is free or holds a matching
run); stderr names the kept entry, why, and where this run went. A failed
history write never loses the `--out` results.

Put `ROOT` before `--compare`: `fleet.py ROOT --out DIR --compare [RUN_DIR]`.
Since `RUN_DIR` is optional, `fleet.py --compare ROOT ...` would read `ROOT` as
the earlier run; the command refuses a `--compare` directory holding neither
`run.json` nor `repos.jsonl` and says so. `--compare=RUN_DIR` is always
unambiguous.

`--compare RUN_DIR` reads the earlier run before scanning and writes
`DIR/compare.json`, with a summary on stderr. A bare `--compare` picks the
latest history entry (by date, then suffix) whose stamp matches this run's
maturity-model version, window, output mode, scans, path hashing, `--fleet-config` hash and `--platform`, preferring one
over the same repository set, so a partial rerun is not taken as the baseline;
with none, stderr says so and nothing is compared. To compare two existing runs without
rescanning:

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_compare.py" EARLIER_DIR LATER_DIR [--write FILE]
```
<!-- chat-skip:end -->

It prints the same JSON to stdout (`--write FILE` replaces FILE atomically) and
exits `0` when compared, `3` when the runs are not comparable (including either
run being at another file version), `2` when a directory is missing or
malformed (a `run.json` missing a key the comparison reads).

**Not comparable.** Runs that differ in maturity-model version, window, counts-only
mode, scan selection, path hashing or `--fleet-config` (`fleet_config_sha256`; a run
with no config and a run from before the key read alike) or `--platform` (`platform`; a run
from before #714 reads as without it), in the plugin's major version (the
`/assess` stats-diff rule), or in file versions (a run from before `run.json`)
give `comparable: false` with every reason in `reasons` and no deltas.

**`compare.json`** (keys sorted, no absolute paths):

| Key | Value |
|-----|-------|
| `comparable`, `reasons`, `notes` | Whether the runs compare; why not; caveats (with hashed paths on both sides, hashes compare only under one `--hash-salt`). |
| `earlier`, `later` | Each run's `scan_date`, `window`, `levels_version`, `plugin_version`, `toolkit_commit`. |
| `movement` | `false` when nothing moved: an unchanged rerun. |
| `repositories` | Measured-set movement across the fleet: `added`, `removed`, `no_longer_scanned` / `newly_scanned` (ok on one side, failed on the other), `no_longer_active` / `newly_active` (the rows' `active` flag). |
| `groups` | Per group: `set` (the same lists for the group) and `set_changed`, `settings_changed`, and `rows`: every maturity model row's `then` and `now` (`{status, level}`, `null` when absent), `moved`, and `cause` when moved. |
| `repos` | Per repository scanned on both sides, the `signals` that changed: dotted `path` into `scans`, `then` and `now` (a side where the leaf is absent omits its key). |

`cause` separates a real change from a change in what was measured: `level`
when the group's set is unchanged, `set` when the group or row is in one run
only or, for a team row, only the set moved, and `set_and_level` when a team
row's set and evidence both moved and the change cannot be split. An org row
reads the group's settings, not its repositories, so on both sides it is always
`level` (`settings_changed` says whether the settings moved).

Three more causes keep a toolkit change from reading as the repository's own move
(`lib/fleet_scanner_diff.py`, #774). They apply to a moved team row present in both runs:

- `set` with `absent_signals`: the row is unknown on one side because that run's scans lack a
  signal the other run holds (a scanner added or dropped it). This is measured-set movement,
  never level movement, and earns or loses nothing.
- `scanner_changed` with `scanner` (each signal block and its changed producer modules under
  `skills/assess/scripts/lib/`): the runs carry different `toolkit_commit`s, git in this
  toolkit checkout shows a producer of a block the row reads changed, and a repository in the
  group changed a signal in that block.
- `toolkit_changed_unknown_scope`: the scanner diff is unavailable (one run with a null
  `toolkit_commit`, an install that is not a git checkout, and the other with one; both null
  at different `plugin_version`s; or a commit git cannot find here). `notes` carries one line saying the scanner diff was
  unavailable. A marketplace install is not a git checkout, so across a plugin update this
  is the usual case there: expect every moved team row to carry it. Run from a git
  checkout of the toolkit holding both commits to get `scanner_changed` instead.

A row whose cause was `set_and_level` keeps `set_moved: true` beside its new cause, so the
measured-set movement stays reported.

A scanner change that alters a signal's meaning ships under a new signal name or a
`signals_schema_version` bump, so an earlier run reads the new signal as absent; these causes
are the backstop for a change that kept the name.

## Coverage: `--discovered`

A repository ghsync never cloned leaves no row, so on its own a run cannot say what it
missed. `--discovered FILE` reconciles the run against ghsync's discovery listing and
reports "N of M discoverable repositories were assessed", each gap named and counted:

```bash
# /ghsync's discovery listing (its --porcelain output works too), then the run against it
ghsync.sh --org my-org --porcelain-json > discovered.jsonl
fleet.py ~/dev/github.com/my-org --discovered discovered.jsonl --out fleet-out
```

Each discovered path lands in one bucket, the first that matches:

| Bucket (`run.json` key) | When |
|-------------------------|------|
| `assessed` | its row is `ok` |
| `failed_scan` | its row is `failed`: cloned, but it could not be scanned (the reason is in `repos.jsonl`). A `missing_checkout` row (a `--from-list` entry with no clone) is not a scan and falls through to the buckets below |
| `archived_or_empty` | `archived` is true or `default_branch` is empty: ghsync skips both |
| `excluded_by_filters` | a `<repo>-main` checkout under `ROOT` this run was not given, such as one a `--from-list` run left out |
| `not_cloned` | anything else: discovered, but there is no checkout |

The buckets sum to `discoverable`. A path names a row the way the root layout does: a
one-segment GitHub path `svc` is the row `<root name>/svc`, and two paths naming the same
row count once. `not_in_listing` counts the rows
no discovered path names; they are left out of the other counts.

ghsync's listing leaves archived and empty repositories out, so `archived_or_empty` counts
the listing's own archived or branchless lines plus the paths in ghsync's archived-repos
cache, `ROOT/.gh_archived_repos`, that the listing lacks. ghsync records no cache of empty
repositories, so an empty one appears only when your list includes it.

`report.md` gives the coverage line first, above every section and diagram, and
`run.json` records the counts under `coverage`. Both are counts only in every output mode:
the coverage line and `coverage` name no repository. (A repository whose scan exits
non-zero is still named by its row and its `logs/<repo>.stderr`, as above.)
Without `--discovered`, the line says the coverage is unknown and `coverage` is `null`.
Logic: `lib/fleet_coverage.py`.

## Output: `report.md` and diagrams

Every run draws diagrams from its own rows, with no model call, and writes them beside
`groups.jsonl` with `DIR/report.md` embedding each one as a relative image link (it renders
in GitHub and GitLab Markdown previews). A diagram that cannot be drawn is skipped and the
report says why in one line; stderr names it too. A diagram failure never changes the exit
code, and the run history still keeps only its three files. `--no-diagrams` turns this off.
The report opens with the [coverage line](#coverage---discovered), then "What changed and who
earned it", before the diagrams (see [`achievements.json`](#output-achievementsjson)).

**Maturity, one number.** The quadrant, the trend lines and the slope chart all plot the
same mean: each `team` row's level as a share of the row's top level, averaged over the rows
with a known level (unknown rows are left out, never counted as 0). `org` rows are left out of
all three: an org row reads the group's settings, not a repository, so the per-repository
quadrant cannot measure it, and the group charts use the same rows so the three read on one
scale. The layer bars draw team rows too and name the org rows under the bars.

| File | Question | Built from |
|------|----------|------------|
| `quadrant.svg` | Which busy repositories are least mature? | Each repository's commits in the window (`ai_usage.total_commits`, non-merge) across, and up its mean maturity: every `team` row of the `--levels` maturity model evaluated on that repository alone, as a share of the row's top level, averaged over the rows that could be measured. The busy (at or above the median) and below-half quadrant is shaded. Skipped without `--levels` or without team rows. |
| `shared-tooling.svg` | What breaks if a shared template changes, and who drifted off it? | `shared_tooling`: repositories linked to the CI sources and marketplaces they reference, with each item's dependent count. Drift: a CI include pinned to a ref other than the fleet's most common ref for that source (ties go to the lexically greatest ref) is dashed, and the SVG caption states this rule; an unpinned include is counted as unpinned, not drift; a pin counts-only could not keep, or a ref that is a CI variable (`$TPL_REF`), is dotted as pin unknown, never unpinned or drift. |
| `trend.svg` | Is each team improving? | The run history: each group's mean maturity (each known team row's level as a share of its `top_level`, the slope chart's unit) in every comparable dated run, oldest left, this run right. Comparable: same window, scans, output mode, path hashing and `--fleet-config` hash, with a maturity model; one run per date, the newest 12. A change of maturity-model version breaks every line, marked by a dashed rule. A history entry written before `groups.jsonl` carried `top_level` takes the top levels from this run's maturity model when it used the same version; otherwise (an older maturity-model version) it is not drawn, so the axis is always 0 to 1 share of top level, and the caption names it ("N earlier runs on an older maturity-model version not shown"). End labels give the last value and the change over the last unbroken stretch. Skipped with fewer than 3 comparable share runs (the skip line names any runs left out), without `--levels` or without team rows, or when the run is not kept in the history (`--no-history`, `--full`). |
| `slope.svg` | What moved since the earlier run, and which row moved it? | The `--compare` result: each group's mean level (a team row's level as a share of its top level) in the earlier run and this one, over the team rows with a known level in both runs, labelled with the row that moved most; a `*` marks a move where the group's measured set of repositories moved too, or the scanner changed (`scanner_changed`, `toolkit_changed_unknown_scope`). Skipped when no earlier run was compared (no `--compare`, or a bare `--compare` matched no history entry), when the runs are not comparable (the reasons are named), without `--levels` or without team rows, or when no group has a team row with a known level in both runs. |
| `layer-bars.svg` | Which layer is the org-wide weak spot? | One bar per `team` row of the maturity model, split by how many repositories reach each level (the heat map's pale-to-dark ramp); repositories whose level is undetermined are a hatched segment and named. Skipped without `--levels`, without team rows, or with no repositories in the run. |

Unknown is never drawn as zero: a repository with no measured row or no commit count, or no
`shared_tooling` evidence, a group with no row known in both compared runs, or a repository
with an undetermined level for a layer, is named on a hatched strip or segment, and a group
with no level in a run breaks its trend line and is named on a hatched note. An unknown row with
a confirmed `floor` is shown as "at least N": a distinct hatched segment in the layer bars, and
beside each known-only mean a floor-inclusive figure labelled as a lower bound (tooltips and
the not-plotted strip), never moving a plotted point. Colours follow the
viewer's light or dark scheme. The SVGs have no script and no external font or link, and the
same run draws the same bytes. Logic: `lib/fleet_diagrams.py`, `lib/fleet_quadrant.py`,
`lib/fleet_tooling_map.py`, `lib/fleet_trends.py`, `lib/fleet_slope.py`, `lib/fleet_layer_bars.py`,
on the shared helpers in `lib/fleet_svg.py`.

## Leadership heat map: `heatmap.py`

`heatmap.py` renders one SVG from a run's `groups.jsonl`, with no rescan: groups
as rows, maturity model rows as columns, each cell the whole level the group reached.
It needs a run made with `--levels`.

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/heatmap.py" fleet-out --levels levels.toml
uv run "${CLAUDE_SKILL_DIR}/scripts/heatmap.py" fleet-out --levels levels.toml --compare last-sprint-out
```
<!-- chat-skip:end -->

| Flag | Meaning |
|------|---------|
| `RUN` | The run's `--out` directory or its `groups.jsonl`. |
| `--levels FILE` | The maturity model file the run used: column titles and each row's top level, so a row's top level is always the darkest colour. Without it the columns show row ids and the level indexes the colour ramp directly. A file whose version or rows differ from the run's exits 2. |
| `--compare EARLIER_RUN` | An earlier run (directory or `groups.jsonl`): each cell shows its change (`+1`, `-1`, `=`, `new`, `was 2`, `was ?`) and groups only the earlier run has are listed. When both are run directories with a `run.json`, the [fleet comparison rule](#run-history-and-comparison) applies: runs that differ in maturity-model version, window, output mode, scans, path hashing, `--fleet-config` hash or plugin major version are not compared (a note says why), and a note names the groups whose measured set moved, so a change there is not read as a level change (`fleet_compare.py` gives each cell's cause). When both are run directories holding `repos.jsonl` but only one has a `run.json` (a run from before the stamp), the pair is refused the same way. A bare `groups.jsonl` (or a directory holding only one) is compared on levels only, refused across maturity-model versions. |
| `--output FILE` | The SVG path (default `heatmap.svg` beside `groups.jsonl`). |

Encoding: the ColorBrewer OrRd ramp (colour-blind safe), pale for level 0 and
darker for a higher level reached, with the level printed in every cell. An
`unknown` cell is white with grey hatching, never level 0: it prints `?` and a
key letter, and the key under the grid names the missing signals. An unknown
cell with a `floor` is the floor's ramp colour under the hatch and prints `N+`
(at least level N, never the exact level); its key says which missing signals
need a full `/assess`. The canvas
has its own white background. The same run gives a byte-identical file. Exit
codes: `0` written, `2` an unreadable or malformed run, a run without maturity model
levels, a mismatched `--levels` or an unreadable `--compare` run.

## Team next-steps pages

`fleet_next_steps.py` turns one run's output into one Markdown page per group,
with no rescan. Pass the maturity model and settings the run used; every page level is
checked against `groups.jsonl` and a difference exits 2.

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_next_steps.py" fleet-out --levels levels.toml --settings settings/
```
<!-- chat-skip:end -->

| Flag | Meaning |
|------|---------|
| `OUT` | The run's `--out` directory. Pages go to `OUT/teams.md` (index) and `OUT/teams/<group>.md`; the previous run's `*.md` pages there are removed first. |
| `--levels FILE` | The maturity model the run used (default: the shipped `skills/assess/levels/default.toml`). A different `version` exits 2. |
| `--settings PATH` | The ghreport settings the run used, in the `fleet.py --settings` forms. |

Each page holds:

- the group's level on every maturity model row, with the roll-up rule;
- for each row below its top level, the unmet criteria of the next level, one
  numbered step per criterion, so a team can take or leave each on its own.
  A step leads with the change and a "done when" a maintainer can check in the
  repository or its platform settings; the signal expression follows as an
  optional re-run check. A team row lists the repositories holding the group
  back, each with its current value, and apart from them the repositories that
  could not be measured; an org row names the settings scope it applies to;
- for an `unknown` row, the signals that were not measured, in how many
  repositories, and how to measure them: a measurement gap, not a failure.
  When measured repositories already fail a higher level, those criteria follow.

Pages are counts-only (ids, counts, numbers, booleans and short tokens; any
other value prints as `set`) and byte-identical when rerendered from the same
run. Exit codes: `0` pages written; `2` a missing or malformed output file, an
invalid maturity model or settings, or inputs that disagree with the run.

## Guarantees

- **Never writes to a target.** Scans run with `GIT_OPTIONAL_LOCKS=0`, from a
  scratch directory, writing their document there; `assess_signals.py` itself
  refuses to write inside its target. The one exception is the opt-in
  `--delete-after`, which removes a whole clone under the rules above.
- **Failure isolation.** A missing checkout, a broken `.git`, a scan that
  crashes, hangs past `--timeout` or writes an unreadable document becomes a
  `failed` row; the other repositories complete. A broken `.git` is checked by
  the runner, since `assess_signals.py` degrades it to unavailable blocks.
- **Deterministic.** Rows are sorted by id, never by completion order. A
  `--platform` run reads live platform state, so its platform blocks can differ
  between two runs on the same day.
- **No network by default.** Only `--platform` makes `gh` or `glab` calls, in
  its own pass; a platform failure never fails a row.

## Optional: repository labels (`--label`)

Off by default. When the user asks for `--label` (or for the fleet to be
labelled or grouped by kind), run this step after `fleet.py` has written
`repos.jsonl`. `fleet.py` itself takes no `--label` flag and never calls a
model; the step is two deterministic commands around one model call.

Each repository gets exactly one label from a closed set: `service`,
`library`, `infrastructure`, `mirror`, `docs`, `other`. Anything else the model
answers becomes `other`. The model is given a compact digest of each
`repos.jsonl` row (its path, status and counts-only facts), never source.

<!-- chat-skip:start -->
1. Count the batches (25 repositories each), then print the prompt for every
   batch, `0` to count minus one:

   ```bash
   uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_labels.py" prompt fleet-out --count
   uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_labels.py" prompt fleet-out --batch 0
   ```

2. For each batch, spawn one `general-purpose` subagent with the Agent tool,
   `model: "haiku"`, giving it the printed prompt verbatim, preceded by one
   line: "Answer from this prompt alone; do not use any tools." The subagent
   has file tools, so that line, not the tool set, is what keeps it off the
   repositories here; the headless form below removes the tools outright.
   Batches are independent: spawn them in one message. Collect every reply,
   unedited, into one answers file.

3. Write and check `labels.jsonl` (`--model` is the model id that answered,
   e.g. `claude-haiku-5-5`; it is recorded on every row):

   ```bash
   uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_labels.py" write fleet-out --model claude-haiku-5-5 --answers answers.txt
   uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_labels.py" check fleet-out
   ```

Outside Claude Code the same model calls work headless, one per batch:

```bash
n=$(uv run fleet_labels.py prompt fleet-out --count)
: > answers.txt
for b in $(seq 0 $((n - 1))); do
  uv run fleet_labels.py prompt fleet-out --batch "$b" | claude -p --model haiku --tools "" >> answers.txt
done
```
<!-- chat-skip:end -->

`write` prints how many repositories were answered, warns when any had no
answer (a skipped batch shows up here), and lists the missing, coerced,
unknown and duplicate paths. Report those to the user; a missing
answer is written as `other` with confidence 0, not retried silently.

**`labels.jsonl`** sits beside `repos.jsonl`, one row per repository, sorted by
`path`, keys sorted. Schema:
[`references/labels.schema.json`](references/labels.schema.json).

| Key | Value |
|-----|-------|
| `path` | The `repos.jsonl` `repo` id: the join key. |
| `label` | One of the six labels. |
| `confidence` | The model's own confidence, 0 to 1; 0 when it gave none or a missing answer. |
| `model` | The model id passed to `write`, never one the model reports. |

**Display and filter only.** Labels may group or filter what is shown to the
user. They never enter `repos.jsonl` or `groups.jsonl`, never change a maturity model
level or which repositories a group roll-up counts, and are never compared
across runs. Both files are byte-identical with and without the label step. Labels are a
model's guess: the counts-only signals say little about what a repository is,
so a label leans mostly on the repository's name. Present them as such.

The logic lives in `skills/assess/scripts/lib/fleet.py`, `lib/fleet_stamp.py`,
`lib/fleet_history.py`, `lib/fleet_compare.py`,
`lib/fleet_activity.py`, `lib/fleet_cleanup.py`, `lib/fleet_diagrams.py` and
`skills/assess/scripts/lib/fleet_labels.py` (see that package's
`lib/README.md`).
