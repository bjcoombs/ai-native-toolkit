---
name: fleet
description: "Run the headless /assess signals across every repository under a ghsync root (or in a list file) and write one counts-only JSON line per repository, deterministically and without writing to any target. Each repository runs assess_signals.py in its own process from a worker pool; one that cannot be scanned is a failed row with a reason token and never stops the run. No model in the gathering path. TRIGGER when the user types /fleet, wants to measure a team, group or whole estate rather than one repository, asks to run /assess signals across all repos in a ghsync checkout or a list of repos, or wants per-repository signal JSONL for a fleet roll-up."
---

# fleet: headless signals across a ghsync root

`/assess` measures one repository; `ghsync` mirrors an estate. `fleet` joins
them: it runs the headless signals entry point (`assess_signals.py`) on every
repository and writes one JSON line per repository plus a run stamp, the same
way every time, so two people measuring the same estate get the same numbers.

It is a pure script: no subagent, no model, no network call.

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
| `--out DIR` | Output directory (required). The run writes `DIR/repos.jsonl` and `DIR/run.json`; a `DIR` inside any target is refused. |
| `--rubric FILE` | A maturity rubric in the `lib/rubric.py` TOML format. Validated now (an invalid rubric exits 2) and its `version` stamped in `run.json`; the per-group roll-up that applies it is a later addition. |
| `--jobs N` | Repositories scanned in parallel (default: CPU count). |
| `--window W` | History window passed to `assess_signals.py` (`6mo`, `90d`, `12w`, `1y`). |
| `--scans a,b` | Only these scans (default: all). |
| `--timeout S` | Seconds one repository may take before its row is marked failed (default 600). |
| `--full` | Keep the full signal blocks, free text included. Default is counts-only. |
| `--hash-paths` | Counts-only paths hashed under one salt drawn for this run: hashes join within the run, but reruns are not byte-identical. |
| `--hash-salt VALUE` | Hash paths under this salt; reruns stay byte-identical and join across runs. |
| `--delete-after` | Delete each clone once it is scanned, to keep disk use flat over a large estate. Narrow and fail-closed; see [`--delete-after`](#--delete-after). |

**Exit codes.** `0`: the run completed and `repos.jsonl` and `run.json` were written, including
when some repositories failed (each is a `failed` row; stderr prints the count
and the failed ids). `2`: usage error - no `ROOT` and no `--from-list`, a root
or list that does not exist, no repositories found, `--out` inside a target, an
invalid `--rubric`, an unknown scan, a bad window, or a `DIR` that cannot be
created (checked before any scan, so `--delete-after` never removes a clone
whose results could not be written).

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
| `rubric_version` | The `--rubric` file's `version`; `null` without `--rubric`. |
| `counts_only` | `false` with `--full`. |
| `paths_hashed` | `true` with `--hash-paths` or `--hash-salt` (never the salt). |
| `scans` | The `--scans` names, sorted; `null` for all. |
| `repositories` | `selected` (every row), `scanned` (rows `ok`), `failed` (rows `failed`) and `qualifying`: scanned repositories whose checkout `HEAD` was committed inside the window, counting back from `scan_date` (the `ghsync --active-since` meaning). |

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
- no stash, no commit on a local branch or `HEAD` that no remote branch holds
  (a clone with no remote is always kept), and no linked worktree;
- it is not, and does not contain, `--out`, the working directory or the toolkit
  itself.

Then the sibling `worktree/` and the repository directory are removed only if
empty. A check that cannot run keeps the clone. stderr prints the deleted and
kept counts and each kept repository with its reason token (`scan_not_ok`,
`not_ghsync_layout`, `symlink`, `not_a_main_clone`, `holds_protected_path`,
`local_changes`, `stash`, `unpushed_commits`, `linked_worktrees`, `git_error`,
`delete_error`). Deletion never changes `repos.jsonl` or `run.json`.

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

The logic lives in `skills/assess/scripts/lib/fleet.py`, `lib/fleet_stamp.py`
and `lib/fleet_cleanup.py` (see that package's `lib/README.md`).
