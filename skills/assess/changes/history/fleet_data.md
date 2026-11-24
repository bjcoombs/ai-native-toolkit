# `FLEET_DATA_VERSION` history

The version history of `FLEET_DATA_VERSION` in `skills/assess/scripts/lib/fleet/fleet_data.py`, newest entry first.
The release PR's `make fold` writes each new entry at the top from the notes in
[`skills/assess/changes/`](../README.md).

<!-- make fold writes each new entry below this line, newest first. -->

5 (4.0.0 release): 14 changes since 4.
- repositories gain ``unit_rows`` (#866): under ``fleet --monorepo-units``, one row per
  monorepo unit with its ``step``, ``next_step``, ``next_blocker`` (the first gate of that Step
  that does not hold) and ``tags``; ``key`` is the unit's folder in ``--full``, its salted hash
  under ``--hash-paths``, else null. Null when the repository was not scanned by unit.
  Additive: a reader of 4 reads it.
- repository row cells gain an optional `floor` (#889): an unknown cell's confirmed low bound,
  at least 1, as `lib.levels.floor_of` reads it; absent otherwise. Additive.
- the document gains `tag_profile` (#940): one row per truth tag, in the fixed tag order, with
  `conditions`, `countable`, `repos`, `measured`, and `median`, `p25` and `p75` of the measured
  repositories' share met, read from each repository's per-tag tally (null stats when none is
  measured, never 0); null without a maturity model. Additive: a reader of 4 reads it, and a
  document without `tag_profile` reads as not measured.
- `opportunities` gains `step_lifts` (#941): the single conditions that would take the most
  repositories to their next Step, each with its row, title, gate, the Step reached and a
  repository count, ranked by count. Counts only, no repository named. Additive: readers of
  the earlier version read it, and an entry stored before it has none.
- fleet-data.json `run` gains `rules_sha256` (issue #952): the sha256 of the rules of the
  maturity model the run scored with, as `run.json` stamps it; null without `--levels` and for
  a stamp written before it. A re-scored history entry carries this run's. `run.json` also
  gains `collectors`, every core collector's `{id, revision}`. Additive.
- repositories gain ``tags`` and units a ``tags`` roll-up (#1073): the per-tag tally, the
  headline's per-tag strip per repository (null when not measured) and, per unit, "N of M
  measured" with each tag's counts summed. Additive: a reader of 4 reads it, and an entry
  without ``tags`` reads as not measured, never zero.
- Steps read gates only (issue #1083): a unit roll-up's `next_step` (`StepHold`) drops
  `threshold` and `below_threshold`, and the per-repository `next_step` drops `threshold`.
- `unit_rows[]` gains `kind` (issue #1098): `skill`, `agents`, `commands` or `package`, the
  unit's kind token, kept in counts-only output; null in rows rebuilt from scans stored
  before units had a kind. Additive: a reader of the current version reads it.
- each repository's row cells and ``opportunities.biggest_lift`` gain ``why`` (#1128): the
  check manifest's why for the criterion named by ``signal`` at its level, or null when there
  is none. Additive: no key moves, and counts-only output keeps it as the toolkit's own text.
- fleet-data.json `run` gains `managed_settings` (issue #1153): whether `fleet.py
  --managed-settings` read the managed settings of the machine that ran the fleet; false for a
  stamp without it. Additive.
- `unit_rows[]` gains `learn_from` (issue #1155): up to three sibling units of the same
  repository that already hold the unit's `next_blocker` gate, the same ecosystem and kind
  first, then by folder. Written like `key`: clear in `--full`, hashed under `--hash-paths`,
  null in plain counts-only output; empty when no sibling holds the gate. Additive.
- repositories gain `learn_from` (issue #1155): up to three measured repositories of the
  run that hold the first gate of the repository's `next_step`, by key; clear in `--full`,
  hashed under `--hash-paths`, null in plain counts-only output and for a repository not
  measured. Additive.
- `report.md`'s Opportunities section ends with a "Learn from" block listing them, and the
  unit rows' `learn_from`, where names are written (issue #1155).
- `repos.unit_rows.kind` gains its counts-only rule (token), so the publish check accepts a
  bundle whose unit rows carry a kind (#1098).

4 (3.0.0 release): two changes since 3.
- repositories gain ``step``, ``next_step`` and ``tally`` (as ``level.json`` gives them,
  owner exclusions applied) and ``exclusions``; units gain a ``step`` roll-up (#944).
  Additive: no existing key moves, so readers of 3 read the new version.
- repositories gain ``gate_blockers`` (#945): every Step gate that does not hold, as the level
  result's blocker for it, so a Step target above the next Step reads its missing gates.
  Additive.

3 (#746): ``opportunities``. Nothing else changed, so readers of 2 read 3.

1 (#703): first version. 2 (#772): the roll-up drops ``mean_share``, ``weakest`` and
``overall`` for ``lowest_level``; repositories drop ``mean_share``. Cells are unchanged.
