---
name: fleet
description: "Run the headless /assess signals across every repository under a ghsync root (or in a list file) and write one counts-only JSON line per repository, deterministically and without writing to any target. Each repository runs assess_signals.py in its own process from a worker pool; one that cannot be scanned is a failed row with a reason token and never stops the run. No model in the gathering path; an optional post-gather step (--label) has a small model label each repository (service, library, infrastructure, mirror, docs, other) into a separate labels.jsonl for display and filtering only. Each counts-only run is kept in a dated run history and can be compared with an earlier run (level movement apart from measured-set movement). TRIGGER when the user types /fleet, wants to measure a team, group or whole estate rather than one repository, asks to run /assess signals across all repos in a ghsync checkout or a list of repos, wants per-repository signal JSONL for a fleet roll-up, wants the repositories in a fleet run labelled or grouped by kind, or asks what moved since the last fleet run or sprint."
---

# fleet: headless signals across a ghsync root

`/assess` measures one repository; `ghsync` mirrors an estate. `fleet` joins
them: it runs the headless signals entry point (`assess_signals.py`) on every
repository and writes one JSON line per repository plus a run stamp, the same
way every time, so two people measuring the same estate get the same numbers.

Gathering is a pure script: no subagent, no model, no network call. The only
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
block (lint suppressions) needs `rg` on `PATH` instead. lizard scores every
tracked source file, so a very large repository can need a longer `--timeout`. `python3 fleet.py ...` without them still runs; those blocks then
read unavailable with a named reason (`no_grimp`, `no_lizard`, `no_rg`) and
their rows `unknown`.

| Flag | Meaning |
|------|---------|
| `ROOT` | A ghsync root: `<root>/<path>/<repo>-main` with a sibling `worktree/`, where `<path>` is the repo name (GitHub) or the `path_with_namespace` at any depth (GitLab). |
| `--from-list FILE` | Scan the repositories listed in FILE instead of discovering them. One entry per line: a `<repo>-main` checkout, its repository directory, a plain git checkout, or (with `ROOT`) a path relative to the root such as `ghsync --porcelain` output. Blank lines and `#` comments are ignored. |
| `--out DIR` | Output directory (required). The run writes `DIR/repos.jsonl`, `DIR/groups.jsonl`, `DIR/run.json`, `DIR/fleet-data.json` and the diagrams (`DIR/report.md`); a `DIR` inside any target is refused. |
| `--levels FILE` | A maturity model in the `lib/levels.py` TOML format (an invalid maturity model exits 2); its `version` is stamped in `run.json`. Each group row carries every maturity model row's whole level, or `unknown`, and the aggregation rule used. Signal paths: see [group roll-up](#output-groupsjsonl). |
| `--settings PATH` | Group settings from `ghreport`: a JSON file holding one settings fragment or an array of them, or a directory of fragment files (a `ghreport --render-dir`, whose `settings/` subdirectory is read). Each fragment is `{scope, provider, two_factor, push_rules, default_branch_protection, rulesets}` with every setting `{value, reason}`. A GitHub fragment may also carry an optional `teams` list, `[{slug, parent, repos}]` (`parent` a team slug or null, `repos` repository names), which places repositories in the [units tree](#output-fleet-datajson). Unreadable input exits 2. |
| `--units FILE` | Org units mapping (TOML): declared units and repository placements for the ownership and system views of [`fleet-data.json`](#output-fleet-datajson). It always overrides the inferred tree. An invalid file, or one naming a parent or unit that does not exist, exits 2 before any scan. |
| `--jobs N` | Repositories scanned in parallel (default: CPU count). |
| `--window W` | History window passed to `assess_signals.py` (`6mo`, `90d`, `12w`, `1y`). |
| `--scans a,b` | Only these scans (default: all). |
| `--timeout S` | Seconds one repository may take before its row is marked failed (default 600). |
| `--full` | Keep the full signal blocks, free text included. Default is counts-only. |
| `--hash-paths` | Counts-only paths hashed under one salt drawn for this run: hashes join within the run, but reruns are not byte-identical. |
| `--hash-salt VALUE` | Hash paths under this salt; reruns stay byte-identical and join across runs. |
| `--delete-after` | Delete each clone once it is scanned, to keep disk use flat over a large estate. Narrow and fail-closed; see [`--delete-after`](#--delete-after). |
| `--compare [RUN_DIR]` | Compare this run with an earlier one and write `DIR/compare.json`. `RUN_DIR` is an `--out` directory or a history entry; a bare `--compare` takes the latest history entry whose stamp matches this run. See [Run history and comparison](#run-history-and-comparison). |
| `--history-dir DIR` | Run history root (default `${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet`). |
| `--history-org NAME` | The `<org>` directory in the history (default: from the repository ids). |
| `--no-history` | Do not keep this run in the history. |
| `--no-diagrams` | Do not draw the [diagrams](#output-reportmd-and-diagrams) (`DIR/report.md` and its SVGs); any a previous run left in `DIR` are removed. |

**Exit codes.** `0`: the run completed and `repos.jsonl` and `run.json` were written, including
when some repositories failed (each is a `failed` row; stderr prints the count
and the failed ids). `2`: usage error - no `ROOT` and no `--from-list`, a root
or list that does not exist, no repositories found, `--out` inside a target, an
invalid `--levels`, `--settings` or `--units` (a settings directory with no `*.json` fragment
file included, or a units file naming a missing unit), a list entry whose `~user` cannot be expanded, an unknown scan, a bad
window, a `DIR` that cannot be created or written to (checked before any scan, so
`--delete-after` never removes a clone whose results could not be written), an
unusable run history location, or a `--compare` directory that is missing or
malformed. A window reaching back before year 1 is accepted: every dated `HEAD` is
inside it.

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
| `scans` | The `assess_signals.py` `scans` map (counts-only unless `--full`); the block shapes are `skills/assess/references/signals.schema.json` and `skills/assess/references/counts-only.md`. `null` when failed. |

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
| `settings_scope` | The settings fragment joined: the one whose `scope` equals the group, else the nearest ancestor (a top-level GitLab group covers its subgroups); `null` when none matches. |
| `settings` | That fragment's settings, each `{value, reason}` or `{available: false, reason}` when ghreport read it as `unknown`. Counts-only (the default) keeps a value that is a short token (`on`, `off`, `full`) and writes any value that is not a short token (a list of rule or ruleset names) as `set`, with an unavailable setting's reason reduced to `unknown` and a value's reason to `null`; `--full` keeps them. `null` without a match. |
| `levels_version` | The maturity model's `version`; `null` without `--levels`. |
| `levels` | One entry per maturity model row: `id`, `scope`, `status` (`level` or `unknown`), `level` (a whole number, `null` when unknown), `top_level` (the row's number of levels, so a level reads as a share of it without the maturity model file; absent in rows written before it was added, which readers handle), `aggregation` (the team rule, `{rule, share}`; `null` for org rows), `missing_signals`, `unknown_repos`. |

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

## Output: `fleet-data.json`

One JSON document per run beside `groups.jsonl`: the versioned bundle the fleet
artifact reads (#692). Keys sorted, compact, every list sorted; the same inputs give the
same bytes. Schema: [`references/fleet-data.schema.json`](references/fleet-data.schema.json).
Logic: `lib/fleet_units.py`, `lib/fleet_repo_facts.py`, `lib/fleet_data.py`. It is kept in
the run history beside the other files.

| Key | Value |
|-----|-------|
| `fleet_data_version` | Document version (1). |
| `run` | The run stamp, as in `run.json`. |
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
   catch-all (`*`) owner that is a team under its group (`@acme/platform`) places it in
   that team;
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

**Roll-ups.** Per unit, one entry in `levels` per `team` row: `mean_share`, the mean of
each measured repository's level as a share of the row's top level (the quadrant's and
trend lines' unit), `measured` of `repos` ("8 of 12 measured": an unknown or failed
repository is counted, never read as 0), and `weakest`, the measured repository with the
lowest share (then key) with its `share` and `level`. `overall` does the same over each
repository's own mean share. `org` rows read a group's settings, not a repository, so
they have no unit roll-up; their levels are in `groups.jsonl`.

**Repositories.** Each has `key` (its id), `status`, `reason`, `active`, `web_url` (the
`origin` remote as `https://<host>/<path>`, credentials, port and `.git` dropped; `null`
for a local remote), `owners` and `owner_count` (the mapping's owners, else the
CODEOWNERS catch-all owners), `units` (`{view, unit}` per view it sits in), `mean_share`,
and `rows`, one cell per `team` row: `status`, `level`, `top_level` and the criterion
behind it. Below the top level, `signal` and `value` are the first unmet criterion of the
next level and its measured value; at the top level both are `null`; an unknown cell
names the signal that could not be read and `reason` (`unavailable`, `type_mismatch`, or
`not_scanned` for a failed repository).

**Privacy.** `--full` keeps everything. Counts-only (the default) keeps keys, tokens and
web URLs, writes a string `value` that is not a short token as `set`, and drops unit
names and owners (`owner_count` stays). With `--hash-paths` or `--hash-salt`, every unit
and repository key, and every reference to one, is hashed (`h:` plus 16 hex digits) and no
name, owner or web URL is written: the file names no repository, group or host. Hashes
join across runs only under one `--hash-salt`. The rules are `FLEET_DATA_RULES` in
`lib/counts_only_rules.py`.

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
| `repositories` | `selected` (every row), `scanned_ok` (rows `ok`), `failed` (rows `failed`) and `qualifying` (rows `ok` with `active` true). |

The stamp holds no path, salt, timing or input mode: a root run, a list run and
any `--jobs` over unchanged checkouts on the same day write the same bytes.

## `--delete-after`

The fleet run did not create the clones, so it deletes only what nothing would
miss. Right after a repository's row is in hand, its checkout
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
maturity-model version, window, output mode, scans and path hashing, preferring one
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
mode, scan selection or path hashing, in the plugin's major version (the
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

## Output: `report.md` and diagrams

Every run draws diagrams from its own rows, with no model call, and writes them beside
`groups.jsonl` with `DIR/report.md` embedding each one as a relative image link (it renders
in GitHub and GitLab Markdown previews). A diagram that cannot be drawn is skipped and the
report says why in one line; stderr names it too. A diagram failure never changes the exit
code, and the run history still keeps only its three files. `--no-diagrams` turns this off.

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
| `trend.svg` | Is each team improving? | The run history: each group's mean maturity (each known team row's level as a share of its `top_level`, the slope chart's unit) in every comparable dated run, oldest left, this run right. Comparable: same window, scans, output mode and path hashing, with a maturity model; one run per date, the newest 12. A change of maturity-model version breaks every line, marked by a dashed rule. A history entry written before `groups.jsonl` carried `top_level` takes the top levels from this run's maturity model when it used the same version; otherwise (an older maturity-model version) it is not drawn, so the axis is always 0 to 1 share of top level, and the caption names it ("N earlier runs on an older maturity-model version not shown"). End labels give the last value and the change over the last unbroken stretch. Skipped with fewer than 3 comparable share runs (the skip line names any runs left out), without `--levels` or without team rows, or when the run is not kept in the history (`--no-history`, `--full`). |
| `slope.svg` | What moved since the earlier run, and which row moved it? | The `--compare` result: each group's mean level (a team row's level as a share of its top level) in the earlier run and this one, over the team rows with a known level in both runs, labelled with the row that moved most; a `*` marks a move where the group's measured set of repositories moved too. Skipped when no earlier run was compared (no `--compare`, or a bare `--compare` matched no history entry), when the runs are not comparable (the reasons are named), without `--levels` or without team rows, or when no group has a team row with a known level in both runs. |
| `layer-bars.svg` | Which layer is the org-wide weak spot? | One bar per `team` row of the maturity model, split by how many repositories reach each level (the heat map's pale-to-dark ramp); repositories whose level is undetermined are a hatched segment and named. Skipped without `--levels`, without team rows, or with no repositories in the run. |

Unknown is never drawn as zero: a repository with no measured row or no commit count, or no
`shared_tooling` evidence, a group with no row known in both compared runs, or a repository
with an undetermined level for a layer, is named on a hatched strip or segment, and a group
with no level in a run breaks its trend line and is named on a hatched note. Colours follow the
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
| `--compare EARLIER_RUN` | An earlier run (directory or `groups.jsonl`): each cell shows its change (`+1`, `-1`, `=`, `new`, `was 2`, `was ?`) and groups only the earlier run has are listed. When both are run directories with a `run.json`, the [fleet comparison rule](#run-history-and-comparison) applies: runs that differ in maturity-model version, window, output mode, scans, path hashing or plugin major version are not compared (a note says why), and a note names the groups whose measured set moved, so a change there is not read as a level change (`fleet_compare.py` gives each cell's cause). When both are run directories holding `repos.jsonl` but only one has a `run.json` (a run from before the stamp), the pair is refused the same way. A bare `groups.jsonl` (or a directory holding only one) is compared on levels only, refused across maturity-model versions. |
| `--output FILE` | The SVG path (default `heatmap.svg` beside `groups.jsonl`). |

Encoding: the ColorBrewer OrRd ramp (colour-blind safe), pale for level 0 and
darker for a higher level reached, with the level printed in every cell. An
`unknown` cell is white with grey hatching, never level 0: it prints `?` and a
key letter, and the key under the grid names the missing signals. The canvas
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
- **Deterministic.** Rows are sorted by id, never by completion order.

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
