---
name: fleet
description: "Run the headless /assess signals across every repository under a ghsync root (or in a list file) and write one counts-only JSON line per repository, deterministically and without writing to any target. Each repository runs assess_signals.py in its own process from a worker pool; one that cannot be scanned is a failed row with a reason token and never stops the run. No model in the gathering path; an optional post-gather step (--label) has a small model label each repository (service, library, infrastructure, mirror, docs, other) into a separate labels.jsonl for display and filtering only. TRIGGER when the user types /fleet, wants to measure a team, group or whole estate rather than one repository, asks to run /assess signals across all repos in a ghsync checkout or a list of repos, wants per-repository signal JSONL for a fleet roll-up, or wants the repositories in a fleet run labelled or grouped by kind."
---

# fleet: headless signals across a ghsync root

`/assess` measures one repository; `ghsync` mirrors an estate. `fleet` joins
them: it runs the headless signals entry point (`assess_signals.py`) on every
repository and writes one JSON line per repository, the same way every time, so
two people measuring the same estate get the same numbers.

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

`python3 fleet.py ...` works the same as `uv run fleet.py ...`; the script has
no third-party dependencies.

| Flag | Meaning |
|------|---------|
| `ROOT` | A ghsync root: `<root>/<path>/<repo>-main` with a sibling `worktree/`, where `<path>` is the repo name (GitHub) or the `path_with_namespace` at any depth (GitLab). |
| `--from-list FILE` | Scan the repositories listed in FILE instead of discovering them. One entry per line: a `<repo>-main` checkout, its repository directory, a plain git checkout, or (with `ROOT`) a path relative to the root such as `ghsync --porcelain` output. Blank lines and `#` comments are ignored. |
| `--out DIR` | Output directory (required). The run writes `DIR/repos.jsonl` and `DIR/groups.jsonl`; a `DIR` inside any target is refused. |
| `--rubric FILE` | A maturity rubric in the `lib/rubric.py` TOML format (an invalid rubric exits 2). Each group row carries every rubric row's whole level, or `unknown`, and the aggregation rule used. Signal paths: see [group roll-up](#output-groupsjsonl). |
| `--settings PATH` | Group settings from `ghreport`: a JSON file holding one settings fragment or an array of them, or a directory of fragment files (a `ghreport --render-dir`, whose `settings/` subdirectory is read). Each fragment is `{scope, provider, two_factor, push_rules, default_branch_protection, rulesets}` with every setting `{value, reason}`. Unreadable input exits 2. |
| `--jobs N` | Repositories scanned in parallel (default: CPU count). |
| `--window W` | History window passed to `assess_signals.py` (`6mo`, `90d`, `12w`, `1y`). |
| `--scans a,b` | Only these scans (default: all). |
| `--timeout S` | Seconds one repository may take before its row is marked failed (default 600). |
| `--full` | Keep the full signal blocks, free text included. Default is counts-only. |
| `--hash-paths` | Counts-only paths hashed under one salt drawn for this run: hashes join within the run, but reruns are not byte-identical. |
| `--hash-salt VALUE` | Hash paths under this salt; reruns stay byte-identical and join across runs. |

**Exit codes.** `0`: the run completed and `repos.jsonl` was written, including
when some repositories failed (each is a `failed` row; stderr prints the count
and the failed ids). `2`: usage error - no `ROOT` and no `--from-list`, a root
or list that does not exist, no repositories found, `--out` inside a target, an
invalid `--rubric` or `--settings`, an unknown scan or a bad window.

## Output: `repos.jsonl`

One line per repository, sorted by `repo`, keys sorted. Schema:
[`references/repos.schema.json`](references/repos.schema.json).

| Key | Value |
|-----|-------|
| `fleet_row_version` | Row envelope version (1). |
| `repo` | The repository id: its path under the root, prefixed with the root's own name when that path is one segment (a GitHub org root gives `org/repo`). A listed entry outside `ROOT` (or with no `ROOT`) takes its `origin` remote's path, else its last two directory names. The same checkout gets the same id from a root run or a list run. |
| `group` | `repo` without its last segment (`grpa/svc1` is in `grpa`). |
| `status` | `ok` or `failed`. |
| `reason` | `null` when ok; a token when failed: `missing_checkout`, `not_a_git_work_tree`, `scan_timeout`, `invalid_output`, `scan_error`, or `scan_exit_<code>`. |
| `signals_schema_version` | The `assess_signals` document version; `null` when failed. |
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
| `settings` | That fragment's settings, each `{value, reason}` or `{available: false, reason}` when ghreport read it as `unknown`. Counts-only (the default) keeps a value that is a short token (`on`, `off`, `full`) and writes any value that is not a short token (a list of rule or ruleset names) as `set`, with reasons reduced to `unknown`; `--full` keeps them. `null` without a match. |
| `rubric_version` | The rubric's `version`; `null` without `--rubric`. |
| `rubric` | One entry per rubric row: `id`, `scope`, `status` (`level` or `unknown`), `level` (a whole number, `null` when unknown), `aggregation` (the team rule, `{rule, share}`; `null` for org rows), `missing_signals`, `unknown_repos`. |

Rubric signal paths are checked at load against `lib/rubric_signals.py` (an
unknown path exits 2 before any scan) and evaluated by `lib/rubric.py`, with no
second threshold config. The shipped `skills/assess/rubrics/default.toml` uses
the same namespace.

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

## Guarantees

- **Never writes to a target.** Scans run with `GIT_OPTIONAL_LOCKS=0`, from a
  scratch directory, writing their document there; `assess_signals.py` itself
  refuses to write inside its target.
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
answers becomes `other`. The model reads a compact digest of each
`repos.jsonl` row (its path, status and counts-only facts), never source.

<!-- chat-skip:start -->
1. Count the batches (25 repositories each), then print the prompt for every
   batch, `0` to count minus one:

   ```bash
   uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_labels.py" prompt fleet-out --count
   uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_labels.py" prompt fleet-out --batch 0
   ```

2. For each batch, spawn one `general-purpose` subagent with the Agent tool,
   `model: "haiku"`, giving it the printed prompt verbatim and nothing else.
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
user. They never enter `repos.jsonl` or `groups.jsonl`, never change a rubric
level or which repositories a group roll-up counts, and are never compared
across runs. Both files are byte-identical with and without the label step. Labels are a
model's guess: the counts-only signals say little about what a repository is,
so a label leans mostly on the repository's name. Present them as such.

The logic lives in `skills/assess/scripts/lib/fleet.py` and
`skills/assess/scripts/lib/fleet_labels.py` (see that package's
`lib/README.md`).
