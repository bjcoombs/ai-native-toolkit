## Output: `achievements.json`

What each unit and repository earned, and the next step for each (#704, parent #692).
Written on every run beside `fleet-data.json`, from run data only: the engine replays
this run and the earlier comparable runs' `fleet-data.json` copies in the run history,
oldest first, every time. It never reads an earlier `achievements.json`, so editing
output cannot change what is earned. Keys sorted, compact; the same history gives the
same bytes. Schema: [`references/achievements.schema.json`](achievements.schema.json).
Logic: `lib/fleet/fleet_achievements.py` (engine), `lib/fleet/fleet_achievements_runs.py` (history),
`lib/fleet/fleet_targets.py` (`--targets`), `lib/fleet/fleet_credit.py` (report section),
`lib/fleet/fleet_rescore.py` (re-score and Recount).

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
`lib/signal_aliases.py`), so a rename causes no Recount line.
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
| `level_up` | A row moves cleanly to a higher level. Each run is compared with the latest earlier run that measured the same repositories, so a member's failed scan on the run before the move delays the credit to the run it returns instead of losing it, and `previous_run` names the run compared with. One move is credited once: each row records its last credited move, up or down, and a move credited after the run compared with bounds the next: only the part of a move outside [the level then, the credited level] is new, so a `level_up` starts above the higher of the two (`from_level`) and a slip falls below the lower. So a member's return neither credits the others' move again, nor counts one drop as two slips, nor reads the full set's unchanged level as a slip from a level credited on a subset, and a real drop and recovery, on a subset or not, is two moves. |
| `first` | Off by default; on with `--first-achievements`. A `level_up` to a level no subject of the same view and kind has held on that row in this fleet's history. Every subject reaching it in the same run is credited: a milestone, never a ranking. A view and kind with one subject (the org root) earns none. |
| `target_hit` | The unit's lowest Step (over its measured repositories, from each repository's `fleet-data.json` `step`) moves cleanly from below a `--targets` Step target to at or above it. Clean means a lowest Step in both runs and the same measured repositories, so a repository that goes unmeasured, fails its scan or joins neither earns nor blocks. Each run is compared with the latest earlier run that measured the same repositories, so a member's failed scan on the run before the crossing delays the credit to the run it returns instead of losing it; `previous_run` names that run. One crossing is credited once: a target already credited after the run compared with is not credited again. A run stored before Steps were in `fleet-data.json` is neutral. It reads Steps, not rows, so it never moves a streak. |
| `streak` | Runs since the subject last slipped (a clean move down on any row) reach 3, 5, 10, 25, 50 or 100. A run with no clean move neither breaks nor extends it; a run after a failed scan compares with the latest earlier run measuring the same repositories, as `level_up` does. |

Each achievement has `kind`, `view`, `unit` (the key `fleet-data.json` writes),
`unit_kind`, `run` and `previous_run` (run ids are scan dates), `evidence` (`row`,
`from_level`, `to_level`, `level`, `from_step`, `to_step`, `target_step`, `runs`, and `moves`: each
repository that moved up, from which level to which, and `criterion`, the next-level
criterion that went from unmet to met; a `target_hit` has `row` null and sets the three
Step fields) and `credit`, one Markdown line.

**Next steps.** `nearest_level_ups` holds one entry per subject and team row below the
top level: among the measured repositories one level below the next, the next-level
criterion most of them share (`signal`), those `repos`, how many more must reach the next
level for the subject to move (`needed`), and the `change` and `done_when` in the team
next-steps pages' wording. `targets` gives each Step target's progress this run: `view`,
`unit`, `step` (the target), `lowest_step` (null when no repository is measured),
`measured` of `repos`, `distance` (Steps to go, 0 when met, null when not measured), the
target Step's `threshold`, `held` (measured repositories below the target Step),
`below_threshold` (of those, the ones whose met tally is below the threshold) and
`missing_gates`: each gate of the target Step that holds back at least one measured
repository, in model order, with `gate`, `row`, `level`, `held` and, of those,
`not_measured` and `excluded` (the owner excluded the gate's condition). The counts are
repositories; a repository's own practices to go are `threshold` less its `tally.met` in
`fleet-data.json`. The gates come from each repository's `gate_blockers`, so a repository
two Steps below the target shows every gate it is missing, not only its next Step's.

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
step = 3                # a [[steps]] entry of the maturity model above Step 1
```

A target names a Step (the default model's 2 Supervised agent PRs, 3 Unattended agent PRs
and 4 Autonomous loops), one per unit and view. It needs `--levels` with `[[steps]]`. The
run exits 2 before any scan on an unknown key or view, a `step` that is not a whole
number from 2 to the top Step, a second target on one unit, a unit that is not in this
run's unit trees, or a repository that is not in the run. The 2.x format that named a
layer `row` with a `level` or a mean `share` was removed in 3.0.0: a file using `row`,
`level` or `share` is refused with an error naming the entry and saying to use `step`.
Credit names units and repositories only, never people. With hashed paths the unit is
hashed under the run's salt before it is matched.

**Report.** `report.md` opens with "What changed and who earned it", before the
diagrams: this run's achievements by kind (up to 20 lines each), then each unit's nearest
level-up (the row needing the fewest repositories), then each Step target: met, or the
unit's lowest Step, the gates to go with the repositories each holds back, and how many
repositories are below the threshold. On the first
run under a new maturity-model version a `### Recount` subsection follows "Earned this
run": the Recount lines, raised moves first (up to 20). Every line is
credit or a next step. With `--no-diagrams` there is no report; `achievements.json` is
still written.
