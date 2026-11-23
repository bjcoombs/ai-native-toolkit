## Output: `repos.jsonl`

One line per repository, sorted by `repo`, keys sorted. Schema:
[`references/repos.schema.json`](repos.schema.json).

| Key | Value |
|-----|-------|
| `fleet_row_version` | Row envelope version (2; 2 added `active`; `head_sha`, `dirty` and `dirty_reason` came later without a bump, so an earlier row reads them as absent). |
| `repo` | The repository id: its path under the root, prefixed with the root's own name when that path is one segment (a GitHub org root gives `org/repo`). A listed entry outside `ROOT` (or with no `ROOT`) takes its `origin` remote's path, else its last two directory names. The same checkout gets the same id from a root run or a list run. |
| `group` | `repo` without its last segment (`grpa/svc1` is in `grpa`); hashed on its own under `--hash-paths` ([Hashed runs](#hashed-runs)). |
| `status` | `ok` or `failed`. |
| `reason` | `null` when ok; a token when failed: `missing_checkout`, `not_a_git_work_tree`, `scan_timeout`, `invalid_output`, `scan_error`, `committed_tree_error` (a dirty checkout whose `HEAD` could not be checked out in the scratch directory), or `scan_exit_<code>`. |
| `signals_schema_version` | The `assess_signals` document version; `null` when failed. |
| `active` | `true` when the committer date of the checkout's `HEAD` falls inside the window (`--window`, default `6mo`, counting back from the run's UTC date in whole days, months and years by calendar), read locally from git; `null` when failed. Close to `ghsync --active-since` but not identical: ghsync counts 30-day months from the current time on platform dates, so cutoffs can differ by a few days. |
| `head_sha` | The `HEAD` commit the row measured; `null` when failed or the checkout has no commit. Counts-only rows keep it in clear, as they keep the repository id. A commit sha identifies its repository (a search for it finds the repository), so under `--hash-paths` or `--hash-salt` it is hashed with the run's salt like the id (`h:` plus 16 hex digits), in `repos.jsonl` and `level.json` alike; `dirty` is always kept. `/assess` counts-only output drops the sha (`measured_commit.head_sha`). |
| `dirty` | `true` when tracked files differ from `HEAD`, by `/assess`'s `measured_commit.dirty` rule: untracked files and the tool's own `.assess/` outputs never count, `.assess/config.toml` does. Unlike `/assess`, edits inside a submodule never count (`--ignore-submodules=dirty`): the committed-tree copy has an empty submodule directory either way, so the same commit scores the same. A moved submodule commit still counts. A dirty checkout is scanned from its committed tree: a `git clone --shared` of it, checked out at `head_sha` in the scratch directory with the `origin` URL copied over, so an uncommitted edit (an `rm CLAUDE.md`, say) never moves the row or its level. Nothing is written into the checkout. Untracked files are ignored in both cases. `null` when failed, and on an ok row only when the dirty state is unknown; `dirty_reason` then says why. With a `HEAD`, an unknown state is never read as clean: the checkout is scanned from its committed tree, as a dirty one is. Fleet's git calls drop the repository-local `GIT_*` variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and the rest of `git rev-parse --local-env-vars`), so a run from a hook or CI step still reads each target. |
| `dirty_reason` | Why an ok row's `dirty` is `null`; `null` whenever `dirty` is `true` or `false`, and on a failed row. `no_commit`: the checkout has no `HEAD`, so there is no commit to check out and it is scanned in place. `status_error`: `git status` failed. `index_read_error`: `git ls-files -v` failed. `sparse_checkout`, `skip_worktree`, `assume_unchanged`: `git status` reported clean, but the index holds entries it never compares (outside a sparse checkout's set, marked skip-worktree without one, or marked assume-unchanged), so the working tree may differ from `HEAD` unseen. ghsync never sets up a sparse checkout or marks entries skip-worktree or assume-unchanged, so these last three come from a checkout changed by hand. |
| `scans` | The `assess_signals.py` `scans` map (counts-only unless `--full`); the block shapes are `skills/assess/references/signals.schema.json` and `skills/assess/references/counts-only.md`. `null` when failed. `scans.review_automation.present` is the row's "AI review in CI: yes / no" (#696): an AI review step or review app config in a git-tracked file, with `kinds` saying which (`ci_action`, `ci_component`, `config_file`). A GitHub review action counts only when its workflow runs on pull requests (`pull_request`, `pull_request_target`, `merge_group`); one wired only to `issue_comment` or `issues` is listed with `on_changes: false`. A GitLab include counts as committed: whether its jobs run in merge-request pipelines is not read. Includes inside a template resolved from the root (see [GitLab includes](../SKILL.md#gitlab-includes)) are matched too, each such match carrying the `depth` it was found at. |

**`logs/<repo>.stderr`.** When a scan process exits non-zero, the last 4000
characters of its stderr go to `DIR/logs/<repo>.stderr` (the id's segments as
directories) for diagnosis. The row keeps only its `scan_exit_<code>` token. A
log is written only for such a failure, and never under `--hash-paths` or `--hash-salt`
(stderr is free text that can name the checkout; stderr on the terminal still names it); each run first removes the `*.stderr`
files a previous run left under `DIR/logs/` (nothing else there is touched).

Rows carry no absolute path, timing, error text or per-repository stamp, so
two runs over unchanged checkouts on the same day are byte-identical (except
with `--hash-paths` and no salt). With `--hash-paths` or `--hash-salt` the
`repo` and `group` ids are hashed; see [Hashed runs](#hashed-runs).

### Hashed runs

With `--hash-paths` or `--hash-salt`, every file under `--out` names a repository and a
group by its hashed id (`h:` plus 16 hex digits under the run's salt), never by its path:
`repos.jsonl` `repo` and `group`, `groups.jsonl` `group`, `settings_scope` and
`unknown_repos`, the `level.json` keys, `doc_freshness.jsonl` `docs_repo` and `repo`,
`agent_clusters.json` `repo_ids` and `group_ids`, `compare.json`, the team pages
(`teams/h-<hex>.md`, no colon in the file name) and every diagram and `report.md`. The
hashed repository id equals the `fleet-data.json` repository key, so the files join on it;
a hashed `group` is one value, so it is no longer `repo` without its last segment. The
kept history entry holds the same hashed ids. Hashes join across runs only under one `--hash-salt`: a
`--hash-paths` run with no salt never joins an earlier run's repositories, and a
same-day rerun goes to a suffixed history entry instead of replacing the day's one.
The group roll-up joins `--settings` by the real group name before hashing. To rebuild
team pages for such a run with `--settings`, pass `fleet_next_steps.py` the same
`--hash-salt`; a `--hash-paths` run with no salt cannot rejoin settings. Terminal output
(stderr) still names repositories. A history entry kept by a hashed run before v2.2.0
holds clear ids, so the first comparison against it shows every repository as
added and removed.
