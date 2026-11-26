# `lib/scoring/levels.py`

The full reference for `skills/assess/scripts/lib/scoring/levels.py`, whose module docstring holds the summary.

Loads a caller-supplied maturity model (TOML via stdlib `tomllib`) and evaluates it
from signals with no model in the path. A criterion over a signal in
`signal_grades.EMPTY_DENOMINATORS` whose set's count reads exactly 0 is `unknown`, with
the table's code as its `reason` (`no_source_files`, #906 and #820; `no_manifests`, #927; `no_instruction_claims` and `no_loaded_instructions`, #827), so row levels and the tally agree. Rows carry an id, a title, a scope (`org`:
once per group from the group settings; `team`: per repository from its
`run-context.json`, rolled up by the declared `min` or `share` rule) and whole levels
numbered 1..K, each a list of criteria (`eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`,
`not_in`) over dotted signal paths. A row reaches level N only when every criterion of
N and of every lower level holds. A missing key, a null, a path through an
`available: false` block or a type mismatch makes the criterion `unknown`. A row then
carries `bounds` (`low`: the definitely-met climb; `high`: below the first definitely
unmet level above it, else K) and is `unknown` only when they differ, so it is never a
false 0 or a false pass. `missing_signals` names every signal whose criterion is
unknown (unavailable or mistyped) even when the level is known. A criterion's `actual`
is the resolved value only when it is a finite scalar, null otherwise, and
`dumps_result` writes with `allow_nan=False`, so a result is always strict JSON (#602). Every level's unmet criteria are in the result for next-step guidance.
A team roll-up applies the rule to the low and the high bounds and reports a level
only when both agree; `unknown_repos` names the repositories whose level is open.
`share_level` is that rule on a list of whole levels (`fleet_achievements` rolls units with it).
`load_levels` / `parse_levels` raise `LevelsError` before evaluation on a missing
version, an unset scope, a gap or duplicate in level numbering, an unknown operator or
key, a missing, empty or unknown `truth` (`MixedTruthError` for a partly tagged model), a bad
`polarity`, `guarded_by`, `eq_signal`, `predicted` or `review` (#1086), or a signal path the pluggable `PathValidator` (`known_paths` for a fixed set)
rejects. `parse_levels(..., known=ids)` accepts a `requires` naming one of `ids`, condition ids defined
elsewhere, and `validate_paths` is the path check on its own: `lib/packs.py` and
`lib/pack_layering.py` use both for a practice pack's rows (#1274). `evaluate` stamps the maturity-model version; `dumps_result` is byte-identical on
rerun. `add_renamed_flag` registers the pre-2.0.0 flag (`RENAMED_FLAG`) on a fleet script's
parser, hidden from `--help`: any use of it exits 2 with one line naming `--levels` (#670).
Tests: `tests/test_levels.py` with fixtures under `tests/fixtures/levels/`; the renamed
flag in `tests/test_fleet_legacy.py`.
An `unknown` result or evaluation whose low bound is at least 1 carries `floor` (#697); `floor_of(entry)` reads it, tolerating its absence.
Two optional model keys feed `level_tally.py` and leave every row level unchanged (#744):
`requires` on a level (condition ids, `<row id>.<level>` from `condition_id`, that must be
met for this condition to count as met; an unknown id, a self-reference or a cycle fails
the load) and `[[steps]]` (`StepDef`: `step` numbered 1..K, `name` and `gates`, each a `StepGate`
`{id, row, level}`). Step 1 has no gates and a later Step keeps every gate of the one
below and adds at least one, or the load fails. A Step needs its gates and nothing else (#1083): a `threshold`
key, the count of met conditions earlier models carried, fails the load with a message
naming its removal. A model without them loads exactly as before.
Each level also carries `truth` (#843), and five additive fields ride along, all defined in
`levels_fields.py`. `load_levels` passes the file path to `parse_levels` (`source=`) so a
`MixedTruthError` names the model file. `LevelsError` and `MixedTruthError` are defined in
`levels_fields.py` and re-exported here, so callers keep importing them from `lib.scoring.levels`.
