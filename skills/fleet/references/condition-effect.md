## Output: `condition_effect.json`

This file reports whether each maturity-model condition goes with better agent outcomes across the fleet: do repositories that meet it have fewer agent commits reverted or reworked (#1086)? It is written with `--levels`, beside `level.json`. A run without `--levels` removes a stale copy.

It is informational only. No Step, count, headline or report section reads it. The exit rule, which demotes a condition that shows no effect by its review date, reads it in a separate step. A plain `/assess` on one repository never reads it.

Logic: `lib/fleet/condition_effect.py` in the assess skill.

**Runs.** It reads this run, from memory, and the comparable earlier runs in the [run history](history.md). An earlier run is comparable when its `run.json` matches this run on window, scans, output mode, path hashing, `--fleet-config` and `--platform`. It reads one run per scan date, before this run's date. With `--no-history` or `--full`, it reads this run alone. Every run is re-scored under this run's maturity model, so a condition reads the same criteria in every run.

**Observations.** For each condition and each outcome measure (`revert_share` and `rework_share` from the `agent_outcome` scan), a repository gives one observation. That observation comes from the newest run in which the condition is met or unmet and the share is a number. It takes one per repository, not one per run, because consecutive runs read overlapping commit windows, so they are not independent samples. A failed row gives no observation, and neither does an unavailable `agent_outcome` block, a null share or a not-measured condition. An unknown never reads as 0.

**Estimate.** `effect` is the mean share of the repositories that meet the condition minus the mean of those that don't. A negative value means meeting the condition goes with fewer agent commits reverted or reworked. `interval` is Welch's interval at `confidence`, and `includes_zero` is true when the interval spans 0, which means no effect was detected. A side with fewer than two repositories has no interval, and `reason` is `too_few_repositories`. `low_confidence` is true when either side has fewer than `min_per_side` repositories. While the history is thin, every result is marked low confidence, and the exit rule demotes nothing on it.

The two thresholds are conventions, chosen by the run 4 lead and open to the maintainer's veto:

| Field | Value | Why |
|---|---|---|
| `min_per_side` | 10 | A common rule of thumb for comparing two means |
| `confidence` | 0.95 | The conventional two-sided level |

**Shape.**

| Key | Meaning |
|---|---|
| `condition_effect_version` | 1 |
| `levels_version` | The maturity-model version every run was re-scored under |
| `runs` | The scan dates read, oldest first, this run last |
| `repositories` | Distinct repositories that gave at least one observation |
| `min_per_side`, `confidence`, `measures` | The thresholds and measures above |
| `conditions[]` | One entry per condition in model order: `id`, `predicted` and `review` from the model (null when unset), and `effects[]` |
| `effects[]` | One entry per measure: `measure`, `met` and `unmet` (repositories on each side), `mean_met`, `mean_unmet`, `effect`, `interval` (`[low, high]` or null), `includes_zero`, `low_confidence` and `reason` |

The file holds counts, shares, dates and condition ids only, with no repository id or path. Numbers are rounded to six places and keys are sorted, so the same history gives the same bytes. A run with hashed paths under a different salt names the same repository under another id, so that repository counts once per salt.
