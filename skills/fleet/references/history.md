## Run history and comparison

Each counts-only run is also kept, so a programme can see what moved since the
last sprint. The run's `repos.jsonl`, `groups.jsonl` and `run.json` (plus
`fleet-data.json`, `demotions.json` and `level.json` with `--levels`, and `doc_freshness.jsonl` when the run wrote one) are copied to `<history>/<org>/<scan_date>/`, where `<history>` is `--history-dir` or
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
fleet run with the same maturity-model version and rules (`rules_sha256`; an entry from
before it is kept apart from a run that records one), window, output mode, scans and path
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
give `comparable: false` with every reason in `reasons` and no deltas. So do runs under
one maturity-model version whose rules differ (`rules_sha256`, #952), for example two
`--levels` files with the same `version`. A run from before #952 recorded no rules hash: it
compares by maturity-model version alone, and `notes` says so. A change in a collector's
`revision` is a note, never a refusal.

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
(`lib/fleet/fleet_scanner_diff.py`, #774). They apply to a moved team row present in both runs:

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
