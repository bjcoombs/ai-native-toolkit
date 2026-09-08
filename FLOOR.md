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
but may never self-apply them. A change to this file, to the floor tokens it
declares, to `scripts/floor_check.py`, `scripts/floor_anchor.py`,
`.github/workflows/floor.yml`, `scripts/contract/`, `scripts/canaries/`, or
`tests/canaries/` takes effect only with the maintainer's explicit,
out-of-band approval, recorded as the maintainer's deployment review of the
`floor-signoff` environment.

<!-- floor-clause:iv -->
**iv. Immutability covers clauses iii and iv.** The immutability rule in clause
iii, and this clause that says so, are themselves part of the floor and cannot
be weakened or removed by any automated step. The floor cannot legislate away
its own protection.

## Tokens

The floor declares its tokens here, one per line. This block is the only source
of the token set: the enforcement script holds no token list of its own, so a
token added below is enforced from the next run onwards, and removing one is a
floor change that goes red rather than a quiet narrowing of the check.

```floor-tokens
<!-- floor:cold-verify-completion -->
start_gate.py
spawn_verifier.py
complete_gate.py
```

The first token is the marker; the rest are the gate invocations a marked file
must keep naming.

## Markers

A file carries a floor obligation when it holds the marker on a line of its own:

    <!-- floor:cold-verify-completion -->

The marked set is not a list anyone maintains. It is discovered at a ref by
searching the tree for that marker and keeping the files that carry it as a
standalone line - a backtick-wrapped mention in prose is documentation, not an
obligation, and this file is excluded because it is where the marker is
defined. To see the set at any ref:

    python3 scripts/floor_check.py markers --base <ref>

Each discovered file is checked against the tokens above, and
`.github/workflows/floor.yml` fails any pull request that removes one of them
from a file that previously carried it (base-vs-head removal detection). So a
retro that guts the instructions while leaving the marker comment intact still
goes red, and a marked file that moves is followed to its new path rather than
read as a deletion.

