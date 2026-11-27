# Flow metrics

A marathon counts merges and watches CPU load. Neither shows the costs that limit an agent team: rework, waiting and integration failures. The flow metrics below measure them, so a retrospective can tell whether adding builders raised throughput or only raised merges of main per PR. They test the rules in [Flow control](flow-control.md).

`scripts/flow_metrics.py` computes every metric from two inputs: the `flow` object in the tracking file, and GitHub API responses saved to a directory. It is deterministic and offline, uses only the standard library, and runs under any `python3` from 3.8. A metric whose input is missing reads `n/a` (null in JSON), never a false 0.

## Metrics

**Per PR**

| Metric | Definition | Source |
|--------|------------|--------|
| Pushes | Distinct head SHAs among the PR's `pull_request` workflow runs | `actions/runs?branch=<head>` |
| Merges of main | PR commits with two or more parents | `pulls/<n>/commits` |
| Review rounds | Distinct commits that received a review | `pulls/<n>/reviews` |
| Green to merged (min) | From the end of the earliest head on which every workflow green at merge was green, to `merged_at` | runs plus `pulls/<n>` |
| Spawn to PR (min) | From the unit's `spawned_at` to the PR's `created_at` | tracking file plus `pulls/<n>` |

**Per run**

| Metric | Definition | Source |
|--------|------------|--------|
| `throughput_per_hour` | Merged PRs per hour of the run window | GitHub |
| `mean_wip`, `peak_wip` | Time-weighted mean and peak of open builder PRs over the run window | GitHub |
| `semantic_conflicts` | Conflicts each PR's own CI passed, caught by the re-check or a merge queue | tracking events |
| `main_red_incidents`, `main_red_min` | Times main went red, and total minutes red; an open incident counts to the window end | tracking events |
| `recheck_failures` | Lead re-checks (main plus the PR) that failed | tracking events |
| `contract_refreezes` | Times the acceptance contract was re-frozen | tracking events |
| `load_peak`, `load_peak_at` | Highest one-minute load average sampled, and when | tracking events |
| `tokens_per_merge` | Total tokens over merged PRs, where the runtime reports usage | tracking `tokens.total` |
| `mean_*` | The mean of each per-PR metric across the run's PRs | computed |
| `dag_width` | Units with no unmet dependency, summed over the waves | tracking `waves` |
| `units_after_combining` | Units spawned after hot-file combining, summed over the waves | tracking `waves` |
| `units_lost_to_combining` | `dag_width` minus `units_after_combining`: the parallelism the repository's hot files cost | tracking `waves` |

**Hot-file combining**

Below the per-run table the script prints one row per wave (DAG width, units after combining, units lost, combines), the top five combining hot files ranked by the combines they caused, and the sequential-merge hot files apart from them. A sequential-merge file (`.claude-plugin/plugin.json` `.version`, a changelog, a lockfile) costs merge order, not combining, so it never ranks as a combining file even when a combine row names it. Each ranked file shows its count in the previous run. A tracking file without `waves` reads every combining metric as `n/a`, and a wave missing either count makes the run totals `n/a`.

The run window runs from `flow.started_at` (else the first spawn or PR) to `flow.ended_at` (else the last merge).

## Recording during the run

Add a `flow` object to the tracking file at Step 2. The Step 2 CRUD removes a unit's `tasks` entry after cleanup, so the flow data lives apart from it and survives the run:

```json
"flow": {
  "run": "<tag>",
  "started_at": "2026-11-22T20:00:00Z",
  "units": {"task-<id>": {"pr": 123, "spawned_at": "2026-11-22T20:05:00Z"}},
  "events": [
    {"type": "semantic_conflict", "at": "<ts>", "pr": 1039, "note": "against #1023"},
    {"type": "recheck_failure", "at": "<ts>", "pr": 1039},
    {"type": "contract_refreeze", "at": "<ts>"},
    {"type": "main_red", "start": "<ts>", "end": "<ts>"},
    {"type": "load", "at": "<ts>", "load1": 122.0}
  ],
  "waves": [
    {"wave": 1, "dag_width": 6, "units": 4,
     "combines": [{"units": ["task-1", "task-2"], "hot_files": ["lib/shared.py"]}],
     "sequential_hot_files": [".claude-plugin/plugin.json"]}
  ],
  "tokens": {"total": 1234567}
}
```

- At spawn, write `units.<unit>.spawned_at`; at PR_CREATED, write its `pr`.
- Append an event when it happens, timed with `date -u +%Y-%m-%dT%H:%M:%SZ`.
- Sample load (`uptime`) at each wave and whenever a full suite runs locally.
- At each wave's spawn, append a `waves` row from the Step 1 plan: `dag_width` is the units with no unmet dependency before combining, `units` the teammates spawned after it, one `combines` entry per combined group naming the hot files that caused it, and `sequential_hot_files` the files merged in order instead.
- Leave `tokens` out when the runtime does not report usage.
- Set `ended_at` at Completion.

## Computing at Completion

Save the GitHub responses for each PR in `flow.units`, one plain command each, into a directory beside the tracking file. `<head>` is the PR's head branch:

```bash
gh api repos/<owner>/<repo>/pulls/<n> > flow/<n>.pull.json
gh api --paginate repos/<owner>/<repo>/pulls/<n>/commits | jq -s 'add' > flow/<n>.commits.json
gh api --paginate repos/<owner>/<repo>/pulls/<n>/reviews | jq -s 'add' > flow/<n>.reviews.json
gh api --paginate "repos/<owner>/<repo>/actions/runs?branch=<head>&per_page=100" | jq -s '{total_count: .[0].total_count, workflow_runs: map(.workflow_runs) | add}' > flow/<n>.runs.json
```

Then compute, with the script path that [Completion](../SKILL.md#completion--retrospective) step 6 resolves (`<skill-dir>` below). Keep the JSON beside the retro log as the next run's `--previous`:

```bash
python3 "<skill-dir>/scripts/flow_metrics.py" --tracking "$TRACK_FILE" --github flow --format json > flow-metrics.json
python3 "<skill-dir>/scripts/flow_metrics.py" --tracking "$TRACK_FILE" --github flow --previous <previous-run>/flow-metrics.json
```

A runs file holding fewer runs than its `total_count` is truncated, and the script reads its pushes and wait as null rather than undercount them.

The markdown output is the retrospective's flow table: one row per PR, one row per run metric with the previous run's value and the change, and a last line naming the largest wait.

## Reading the table

- **Throughput flat while `mean_wip` rose:** the extra builders queued behind the merge step. Lower the WIP limit.
- **`mean_merges_of_main` above 1:** PRs waited long enough for main to move under them. That is integration cost, not progress.
- **The largest wait:** read why it waited (a freeze, a hold, a question, the review bot) and name the cause in "What didn't work".
- **`semantic_conflicts` or `main_red_min` above 0:** look for a feature chain that was not re-checked ([Flow control](flow-control.md) rule 5).
- **`contract_refreezes` above 0:** check whether a criterion pinned a count over a growing manifest (rule 2).
- **`units_lost_to_combining` above 0:** the top combining hot file is the structural fix to weigh first (split it, or stop generating it into every PR). The next run's figure shows whether the fix widened the team.
