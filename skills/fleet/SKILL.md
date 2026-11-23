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
pass the opt-in [`--platform`](references/platform.md#platform-pass---platform). The only
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
model's code design and architecture blocks (the import graph and file size); the
linters block (lint suppressions) needs ripgrep (`rg`) instead. lizard scores every
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
(the runtime legibility and doc-freshness rows get a level). Some blocks are measured
only by a full `/assess` run of one repository and always read unavailable
here, with the reason code `not_registered`: `dead_code`, `runtime` and
`understanding` (the costly scans, opt-in to a full run), plus the blocks a full
run assembles from its own state (`diff`, `behaviour`, `documentation`,
`derived_findings`, `coverage_report`, `test_pressure`, `test_focus`,
`accretion_ratchet`, `gap_actions`, `ancestor_instruction_files`). Blocks that
read GitHub or GitLab (`config_drift`, `review_reality`, `gate_cost_estimate`) read
unavailable with the code `network`: a fleet run makes no network call without
`--platform`. Review on merged pull requests, level 1, reads `review_evidence`, which an AI review step
committed to CI (`review_automation`) sets offline, so such a repository reads
"at least 1" on that row without `--platform`. Doc
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
| `--out DIR` | Output directory (required). The run writes `DIR/repos.jsonl`, `DIR/groups.jsonl`, `DIR/agent_clusters.json`, `DIR/run.json`, `DIR/fleet-data.json`, `DIR/achievements.json`, with `--levels` `DIR/level.json`, `DIR/condition_effect.json` and `DIR/demotions.json`, and the diagrams (`DIR/report.md`); a `DIR` inside any target is refused. |
| `--levels FILE` | A maturity model in the `lib/levels.py` TOML format (an invalid maturity model exits 2); its `version` is stamped in `run.json`. Each group row carries every maturity model row's whole level, or `unknown`, and the aggregation rule used. Signal paths: see [group roll-up](references/groups.md#output-groupsjsonl). |
| `--settings PATH` | Group settings from `ghreport`: a JSON file holding one settings fragment or an array of them, or a directory of fragment files (a `ghreport --render-dir`, whose `settings/` subdirectory is read). Each fragment is `{scope, provider, two_factor, push_rules, default_branch_protection, rulesets}` with every setting `{value, reason}`. A GitHub fragment may also carry an optional `teams` list, `[{slug, parent, repos}]` (`parent` a team slug or null, `repos` repository names), which places repositories in the [units tree](references/fleet-data.md#output-fleet-datajson); `ghreport` writes it on a GitHub org fragment. Unreadable input exits 2. |
| `--units FILE` | Org units mapping (TOML): declared units and repository placements for the ownership and system views of [`fleet-data.json`](references/fleet-data.md#output-fleet-datajson). It always overrides the inferred tree. An invalid file, or one naming a parent or unit that does not exist, exits 2 before any scan. |
| `--fleet-config FILE` | Fleet settings (TOML) passed to every repository scan. Validated before any scan: an unknown section or key, or a value of the wrong kind, exits 2 and names the key. See [Fleet settings](references/fleet-config.md#fleet-settings---fleet-config). |
| `--platform` | After the offline scan, run a separate platform pass that reads GitHub or GitLab through `gh` or `glab` and joins its blocks into each `ok` row. Off by default: without it no network call is made. See [Platform pass](references/platform.md#platform-pass---platform). |
| `--platform-jobs N` | Repositories read in parallel by the platform pass (default: `[platform] jobs`, else 4). Needs `--platform`. |
| `--platform-timeout S` | Seconds one repository's platform read may take before its platform blocks are unavailable (default: `[platform] timeout`, else 300). Needs `--platform`. |
| `--jobs N` | Repositories scanned in parallel (default: CPU count). |
| `--window W` | History window passed to `assess_signals.py` (`6mo`, `90d`, `12w`, `1y`). |
| `--scans a,b` | Only these scans (default: all), plus what they read. `review_reality` also brings `review_automation` and `review_evidence`, the review-on-merged-pull-requests level 1 signal. |
| `--timeout S` | Seconds one repository may take before its row is marked failed (default 600). |
| `--full` | Keep the full signal blocks, free text included. Default is counts-only. |
| `--monorepo-units` | Scan each repository by monorepo unit too (`assess_signals.py --units`): each repository in `fleet-data.json` gains `unit_rows`, one Step, next blocking gate, per-tag tally and "learn from" siblings (the units of the same repository that already hold that gate, #1155) per unit. Needs `--levels` (exit 2 without it). A unit is named only by its folder's hash under `--hash-paths` or `--hash-salt`; in plain counts-only output a unit row's `key` is `null`, so it cannot be matched to a folder or to the next run (use `--hash-salt` to join runs, or `--full`). One scoped pass per unit, so it adds scan time on a large monorepo. See [Unit rows](references/fleet-data.md#unit-rows). |
| `--hash-paths` | Counts-only paths and repository ids hashed under one salt drawn for this run: hashes join within the run, but reruns are not byte-identical. See [Hashed runs](references/repos.md#hashed-runs). |
| `--hash-salt VALUE` | Hash paths and repository ids under this salt; reruns stay byte-identical and join across runs. |
| `--delete-after` | Delete each clone once it is scanned, to keep disk use flat over a large estate. With `--platform` no clone is deleted until the whole offline scan is done, so peak disk use is the whole estate. Narrow and fail-closed; see [`--delete-after`](#--delete-after). |
| `--compare [RUN_DIR]` | Compare this run with an earlier one and write `DIR/compare.json`. `RUN_DIR` is an `--out` directory or a history entry; a bare `--compare` takes the latest history entry whose stamp matches this run. See [Run history and comparison](references/history.md#run-history-and-comparison). |
| `--history-dir DIR` | Run history root (default `${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet`). |
| `--history-org NAME` | The `<org>` directory in the history (default: from the repository ids). |
| `--no-history` | Do not keep this run in the history. |
| `--discovered FILE` | What ghsync discovered: its `--porcelain` output (one path per line) or `--porcelain-json` output (one object per line). `report.md` and `run.json` then say how many of these repositories were assessed and count each gap. Needs `ROOT`; a malformed line exits 2 and names the line. See [Coverage](#coverage---discovered). |
| `--name-agent-clusters` | Name the member repositories and groups of each cluster in `DIR/agent_clusters.json`. Off by default, in counts-only and `--full` alike: a cluster then carries counts and SDK slugs only. See [agent clusters](references/agent-clusters.md#output-agent_clustersjson). |
| `--targets FILE` | Targets (TOML) for [`achievements.json`](references/achievements.md#output-achievementsjson): each `[[target]]` names a `view`, a `unit` and a `step` above Step 1 (see the [targets file format](references/achievements.md#output-achievementsjson)). Needs `--levels` with `[[steps]]`. An invalid file, an old `row`/`level`/`share` file, or a unit not in the run exits 2 before any scan. Not part of the run stamp, so changing a target never stops runs comparing. |
| `--first-achievements` | Also credit `first` achievements in [`achievements.json`](references/achievements.md#output-achievementsjson) (a unit reaching a level no unit of its view and kind has held on that row in this fleet's history). Off by default (#772), since it compares a unit with its peers. Not part of the run stamp. |
| `--no-diagrams` | Do not draw the [diagrams](references/report-and-diagrams.md#output-reportmd-and-diagrams) (`DIR/report.md` and its SVGs); any a previous run left in `DIR` are removed. |

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

## Output files and references

- [`repos.jsonl`](references/repos.md): one line per repository, and hashed runs.
- [`doc_freshness.jsonl` (opt-in)](references/doc-freshness.md): the opt-in doc-freshness rows.
- [`groups.jsonl`](references/groups.md): the group roll-up.
- [`level.json`](references/level.md): per-repository maturity-model levels.
- [`fleet-data.json`](references/fleet-data.md): units, roll-ups, repositories and opportunities.
- [`achievements.json`](references/achievements.md): achievements, next steps and the targets file.
- [`condition_effect.json` and `demotions.json`](references/condition-effect.md): each condition's estimated effect on agent outcomes, and the exit rule's demotions.
- [Database projection](references/publish.md): the artifact database writes, access rules and size guard.
- [`agent_clusters.json`](references/agent-clusters.md): agent clusters.
- [`run.json`](references/run.md): the run stamp.
- [`--fleet-config`](references/fleet-config.md): the `--fleet-config` sections and keys.
- [`--platform`](references/platform.md): the platform pass.
- [Run history and comparison](references/history.md): the run history, `--compare` and `compare.json`.
- [`report.md` and diagrams](references/report-and-diagrams.md): `report.md` and its diagrams.

## GitLab includes

Each scan of a ROOT run (and of a `--from-list` run given ROOT) gets `assess_signals.py --ghsync-root ROOT`. The top-level `include:` of a repository's `.gitlab-ci.yml` is then resolved offline (`skills/assess/scripts/lib/ci_includes.py`, #766): `local:` files of the same checkout, `project: <path>` / `file:` / `ref:` files from the clone `ROOT/<path>/<last segment>-main` (each segment matched ignoring case, as GitLab paths are, so the result is the same on every OS), and `component: <host>/<project>/<name>@<version>` from `templates/<name>.yml` (or `templates/<name>/template.yml`) of the clone of `<project>`, each nested include in turn to a bounded depth. A pinned `ref:` is read at that ref when the clone holds it; a pin it does not hold (or a CI-variable ref) is `ref_missing`, never read at another version. No `ref:` reads the clone's HEAD (`ref_basis: head`). `scans.ci_checks` then counts the check lines of the resolved files (`included_check_commands`) and lists one record per include item in `includes` (`kind`, `id`, `status`, `ref_basis`, `depth`; no file name or text), with `resolved_includes` and `unresolved_includes`. `test_or_lint_configured` is true when an own or included line runs a check, false when every include resolved and none does, and null while any include is unresolved: a template that is not cloned (`status: not_cloned`), a `template:` or `remote:` include (`not_offline`), a pin the clone does not hold, a glob, or a depth or file-count limit. A `project:` or `component:` path resolves from ROOT, so ROOT is the ghsync root that holds each `path_with_namespace`; a fleet ROOT below it (one group's directory) reads `not_cloned` for those includes, which is unknown rather than wrong. `scans.review_automation` matches the include entries of resolved files the same way it matches the repository's own. No `gh` or `glab` call is made. With `--delete-after` no root is passed, since a template clone could be deleted before a repository that includes it is scanned, so those includes stay unknown.

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
cache, `ROOT/.gh_archived_repos`, that the listing lacks. The cache reflects ghsync's last
run against ROOT, so a listing narrower than that run picks up every archived path in the
cache, including ones outside the listing's scope. ghsync records no cache of empty
repositories, so an empty one appears only when your list includes it.

`report.md` gives the coverage line first, above every section and diagram, and
`run.json` records the counts under `coverage`. Both are counts only in every output mode:
the coverage line and `coverage` name no repository. (A repository whose scan exits
non-zero is still named by its row and its `logs/<repo>.stderr`, as in [`repos.jsonl`](references/repos.md#output-reposjsonl).)
Without `--discovered`, the line says the coverage is unknown and `coverage` is `null`.
Logic: `lib/fleet/fleet_coverage.py`.

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
| `--compare EARLIER_RUN` | An earlier run (directory or `groups.jsonl`): each cell shows its change (`+1`, `-1`, `=`, `new`, `was 2`, `was ?`) and groups only the earlier run has are listed. When both are run directories with a `run.json`, the [fleet comparison rule](references/history.md#run-history-and-comparison) applies: runs that differ in maturity-model version, window, output mode, scans, path hashing, `--fleet-config` hash or plugin major version are not compared (a note says why), and a note names the groups whose measured set moved, so a change there is not read as a level change (`fleet_compare.py` gives each cell's cause). When both are run directories holding `repos.jsonl` but only one has a `run.json` (a run from before the stamp), the pair is refused the same way. A bare `groups.jsonl` (or a directory holding only one) is compared on levels only, refused across maturity-model versions. |
| `--output FILE` | The SVG path (default `heatmap.svg` beside `groups.jsonl`). |

Encoding: the ColorBrewer YlGn growth ramp (colour-blind safe), pale yellow for level 0 and
darker for a higher level reached, with the level printed in every cell. An
`unknown` cell is white with grey hatching, never level 0: it prints `?` and a
key letter, and the key under the grid names the signals not measured yet. An unknown
cell with a `floor` is the floor's ramp colour under the hatch and prints `N+`
(at least level N, never the exact level); its key says which unmeasured signals
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
| `OUT` | The run's `--out` directory. Pages go to `OUT/teams.md` (index) and `OUT/teams/<group>.md` (`teams/h-<hex>.md` for a hashed group); the previous run's `*.md` pages there are removed first. |
| `--levels FILE` | The maturity model the run used (default: the shipped `skills/assess/levels/default.toml`). A different `version` exits 2. |
| `--settings PATH` | The ghreport settings the run used, in the `fleet.py --settings` forms. |
| `--hash-salt VALUE` | The `--hash-salt` the run used. Needed with `--settings` when the run hashed its group names ([Hashed runs](references/repos.md#hashed-runs)); the recorded `settings_scope` is joined through it. |

Each page holds (every heading opening with an `In place:` line, as in [`report.md`](references/report-and-diagrams.md#output-reportmd-and-diagrams)):

- the group's level on every maturity model row, with the roll-up rule;
- for each row below its top level, the unmet criteria of the next level, one
  numbered step per criterion, so a team can take or leave each on its own.
  A step leads with the change and a "done when" a maintainer can check in the
  repository or its platform settings, with a "Why" line from the check
  manifest between the two when the criterion has one; the signal expression
  follows as an optional re-run check. A team row lists the repositories holding the group
  back, each with its current value, and apart from them the repositories that
  could not be measured; an org row names the settings scope it applies to;
- for an `unknown` row, the signals that were not measured, in how many
  repositories, and how to measure them: a measurement gap, not a setback.
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

The logic lives in `skills/assess/scripts/lib/fleet/runner.py`, `lib/fleet/fleet_stamp.py`,
`lib/fleet/fleet_history.py`, `lib/fleet/fleet_compare.py`,
`lib/fleet_activity.py`, `lib/fleet/fleet_cleanup.py`, `lib/fleet/fleet_diagrams.py` and
`skills/assess/scripts/lib/fleet/fleet_labels.py` (each module's docstring documents it).
