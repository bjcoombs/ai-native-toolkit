# FLOOR

This file is the constitutional floor of the acceptance-contract workflow. It
states the invariants that the workflow may never rewrite for itself. The
learning loop (retro) may *propose* changes to anything in this file, but it may
never *apply* them: the floor changes only with the maintainer's out-of-band
sign-off (clause iii), and that rule is itself immutable (clause iv).

The floor is enforced mechanically. `.github/workflows/floor.yml` is a required
status check on the default branch; it fails any pull request that removes a
floor marker or a gate invocation from a marked file, that guts the four clauses
below, or that finds the floor's own enforcement no longer wired into repo
settings. See `docs/floor-anchor-proof.md` for the external-anchor proof.

## Clauses

<!-- floor-clause:i -->
**i. Cold verification is part of run-complete, every run.** A run is not
complete until a fresh, non-implementing agent has executed the frozen
acceptance contract against the assembled product and recorded the observed
results. The party that writes the code never grades its own outcome. This
holds for every run, from every work source.

<!-- floor-clause:ii -->
**ii. The contract freezes before decomposition and is unamendable.** The
acceptance contract is authored and frozen (its sha256 recorded) before the run
is decomposed into tasks, and it is unamendable for the life of the run. If
mid-run evidence shows the contract itself is wrong, the run aborts to
authoring and re-freezes; it is never edited in place. There is no amendment
door and no free skip door.

<!-- floor-clause:iii -->
**iii. Changes to the floor require the maintainer's out-of-band sign-off.** The
retro, and any other automated learning step, may propose changes to this floor
but may never self-apply them. A change to `FLOOR.md`, the floor markers, the
gate invocations, `.github/workflows/floor.yml`, `scripts/contract/`,
`scripts/canaries/`, or `tests/canaries/` takes effect only with the
maintainer's explicit, out-of-band approval.

<!-- floor-clause:iv -->
**iv. Immutability covers clauses iii and iv.** The immutability rule in clause
iii, and this clause that says so, are themselves part of the floor and cannot
be weakened or removed by any automated step. The floor cannot legislate away
its own protection.

## Markers

The following literal token marks each file that carries a floor obligation:

    <!-- floor:cold-verify-completion -->

It appears in `skills/marathon/SKILL.md`, `skills/pr-review-merge/SKILL.md`,
`commands/tm.md`, and `commands/issues.md`. Alongside it, those files carry the
literal gate invocations `start_gate.py`, `spawn_verifier.py`, and
`complete_gate.py`. `.github/workflows/floor.yml` fails any PR that removes a
marker or an invocation from a file that previously carried it (base-vs-head
removal detection), so a retro that guts the instructions while leaving the
marker comment intact still goes red.
