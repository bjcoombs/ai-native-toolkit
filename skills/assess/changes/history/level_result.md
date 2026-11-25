# `LEVEL_RESULT_VERSION` history

The version history of `LEVEL_RESULT_VERSION` in `skills/assess/scripts/lib/scoring/level_tally.py`, newest entry first.
The release PR's `make fold` writes each new entry at the top from the notes in
[`skills/assess/changes/`](../README.md).

<!-- make fold writes each new entry below this line, newest first. -->

4 (4.0.0 release): three changes since 3.
- each result gains `tags` (#940): one row per truth tag, every tag of `TRUTH_TAGS` in that
  order, with `conditions`, `countable` (by grade, not a hazard), `set_aside` (countable and
  excluded by the owner or declined), `met` and `measured` (the headline strip cell's, one
  tally), `not_measured` (`{id, reason}`) and `countable_not_measured` (its countable subset,
  which says why a row has no share). Additive: no key moves, and a reader treats a result
  without `tags` as written before them.
- Steps read gates only (issue #1083): `next_step` drops `threshold`, and a blocker is always
  `kind: "gate"`; the `threshold` kind and its `threshold` and `met` fields are gone.
  Breaking: a reader of the threshold blocker finds none.
- a condition may carry `disposition: "demoted_by_exit_rule"` (#1086): the fleet exit
  rule demoted it, so it keeps its state and is listed, never counted. Only `fleet.py`
  sets the model's `demoted` ids (`with_demotions`), which every fleet result of the run
  reads; a gate condition is never demoted, so no Step moves. `/assess` results are unchanged.

3 (3.0.0 release): three changes since 2.
- a condition may carry ``polarity: "hazard"``, and a hazard is never counted (#843).
- owner exclusions add the optional ``excluded`` list and the condition and blocker
  fields ``disposition`` and ``exclusion_kind`` (#910); results without exclusions are
  unchanged.
- each condition may carry `why`, the check manifest whys of its criteria joined by one
  space (#939); absent for a criterion with no manifest entry. Additive: no key moves.
