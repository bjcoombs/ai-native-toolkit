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
declares, to `scripts/floor_check.py`, `scripts/floor_anchor.py`, or
`.github/workflows/floor.yml` takes effect only with the maintainer's explicit,
out-of-band approval, recorded as the maintainer's deployment review of the
`floor-signoff` environment. The gate code and the canaries the clauses above
rely on (`scripts/contract/`, `scripts/canaries/` and `tests/canaries/`) are
gated by the canary suite rather than by this sign-off. The one exemption is a
pull request authored and pushed by `dependabot[bot]` whose floor-core change
is only like-for-like `uses:` action pin bumps, each with a new commit and a
new version comment, in workflow files, as judged by the base ref's copy of
`scripts/floor_check.py pin-bump-only`; any other change, author or pusher
still needs the sign-off.

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

## The merge queue

`main` may merge through GitHub's merge queue, which tests a temporary branch
holding `main` plus the queued pull requests ahead of a pull request plus that
pull request (a merge group), and merges only when every required check passes
on it. The floor's behaviour there:

- **`floor enforcement` runs on every merge group.** Marker removal is judged
  against the group's base commit, so it covers every pull request in the
  group at once, and the four clauses are checked on the group's head.
- **`floor self-anchor` runs on every merge group,** reading the live branch
  protection and the `floor-signoff` environment as it does on a pull request.
- **The sign-off is a pull-request gate.** The path filter does not classify a
  merge group (both of its answers are false), so no deployment review is
  requested inside the queue and the canary suite does not run there. A
  floor-core pull request still cannot reach the queue unapproved: the queue
  admits only a pull request whose required checks passed on its head, and
  `floor enforcement` on that head is red until the maintainer approves the
  review (clause iii). A push to a queued pull request removes it from the
  queue, so the approved head is the head that merges. An administrator merge
  bypasses the queue, not the review: with `enforce_admins` on, branch
  protection still requires `floor enforcement`.
- **A workflow that stops triggering on merge groups fails closed.** Its
  required context never reports on the group, and the queue removes the pull
  request when the check timeout expires; nothing merges.
