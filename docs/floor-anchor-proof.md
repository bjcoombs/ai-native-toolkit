# Floor Anchor Proof (PRD criterion 5 / E2)

The floor's markers and clause checks only bite while `.github/workflows/floor.yml`
is a **required** status check on the default branch and while `floor.yml` itself
is **path-restricted** against self-merge edits. This document records the proof
that both hold under the solo-merge setup, via two adversarial test PRs.

Both attack PRs are opened on throwaway branches, **never merged**, and closed
after the outcome is recorded. Neither branch is the feature branch.

## Repo settings under test

`floor.yml` runs the floor in clearly separated jobs so a reader can see which
layer is red: `floor enforcement` (the deterministic marker-removal +
FLOOR.md-integrity layer, self-contained and green when honest) and
`floor self-anchor` (the E2 repo-settings anchor, fail-closed). A third job,
`canary suite (semantic layer)` (E3), runs the real-gate canary harness only on
PRs that touch the workflow skills or gate/canary code (gated by a `canary path
filter` job); it is conditional and skippable, so - unlike the two below - it is
**not** a required status check and is not part of the anchored topology proved
here.

The enforcement topology this floor relies on (configured out-of-band by the
maintainer -- FLOOR.md clause iii):

1. **Required status checks.** Both floor job names -- `floor enforcement` and
   `floor self-anchor` -- are required status checks on `main`, added alongside
   the existing required checks (`skills/assess pytest`, `scripts/ pytest`,
   `plugin contract pytest`, `Validate PR title`) without disturbing them. Two
   contexts, so neither the deterministic layer nor the anchor layer can be
   silently dropped.
2. **Path restriction.** An active push ruleset with a `file_path_restriction`
   rule covering `.github/workflows/floor.yml`, requiring maintainer bypass to
   modify that path.
3. **Anchor read token.** `FLOOR_ANCHOR_TOKEN` repo secret -- a fine-grained PAT
   with `Administration: read` -- so the `floor self-anchor` job can query the
   settings above. (The default `GITHUB_TOKEN` cannot read branch protection or
   rulesets.)

Until all three are configured, the `floor self-anchor` job fails closed and
prints the exact commands below in its log and job summary; the `floor
enforcement` deterministic job is unaffected and stays green.

Exact configuration commands are in the "Configuration commands" section below so
the state is reproducible.

## Attack A -- FLOOR.md clause removal

**Branch:** `floor-attack-a-marker-removal`
**Attack:** remove a clause (its anchor + key phrase) from `FLOOR.md`.
**Expected:** `floor.yml`'s clause-integrity check fails; because `floor enforcement`
is a required check, the PR cannot merge.

> Scope note: the canonical criterion-5 attack removes the
> `<!-- floor:cold-verify-completion -->` marker from `skills/marathon/SKILL.md`.
> That marker is added later this marathon (the task that wires the gates into
> the marathon skill). Until then the marker-removal path has nothing to remove,
> so attack A exercises the equivalent floor-integrity failure via FLOOR.md
> clause removal, which is armed from this PR. See the pending row below.

| Field | Value |
|---|---|
| PR number | _pending: run after settings configured_ |
| floor.yml result | _expected: FAIL at "FLOOR.md four-clause integrity"_ |
| Mergeable? | _expected: no (required check red)_ |
| Outcome | _pending_ |

## Attack B -- gut floor.yml itself

**Branch:** `floor-attack-b-gut-workflow`
**Attack:** replace `.github/workflows/floor.yml` with a no-op that always passes.
**Expected:** the push ruleset's path restriction refuses the change to
`.github/workflows/floor.yml`; a plain (non-bypass) merge is blocked.

| Field | Value |
|---|---|
| PR number | _pending: run after settings configured_ |
| Push/merge result | _expected: blocked by file_path_restriction ruleset_ |
| Mergeable? | _expected: no (path restricted, maintainer bypass required)_ |
| Outcome | _pending_ |

## Pending: canonical marathon-marker attack (post-task-9)

The canonical marker-removal attack on `skills/marathon/SKILL.md` re-runs after the
marathon-skill task lands the `<!-- floor:cold-verify-completion -->` marker and
the gate invocations. The marathon lead owns that re-run.

| Field | Value |
|---|---|
| PR number | _pending: post-marker task_ |
| floor.yml result | _expected: FAIL at "Marker removal detection (base-vs-head)"_ |
| Mergeable? | _expected: no (required check red)_ |
| Outcome | _pending: re-run after the marathon-skill marker lands_ |

## Configuration commands

Reproducible settings, run by the maintainer (owner/admin) out-of-band:

```bash
REPO=bjcoombs/ai-native-toolkit

# 1. Add the floor check to the existing required checks on main (classic
#    branch protection), preserving the four existing contexts.
gh api "repos/$REPO/branches/main/protection/required_status_checks" \
  --method PATCH \
  -f 'checks[][context]=skills/assess pytest' \
  -f 'checks[][context]=scripts/ pytest' \
  -f 'checks[][context]=plugin contract pytest' \
  -f 'checks[][context]=Validate PR title' \
  -f 'checks[][context]=floor enforcement' \
  -f 'checks[][context]=floor self-anchor'

# 2. Path-restrict floor.yml via a push ruleset (maintainer bypass).
#    DESCOPED (2026-07-10): this POST is known to fail with HTTP 422 on public,
#    user-owned repos ("Source public repos cannot have push rules" / "Source
#    only org-owned repos can have push rules"). Retained for the org-migration
#    case only -- if this repo ever moves into an organization, run it to restore
#    the mechanical path lock. See the "Honest-degrade note (E2)" below.
gh api "repos/$REPO/rulesets" --method POST --input - <<'JSON'
{
  "name": "floor-workflow-path-lock",
  "target": "push",
  "enforcement": "active",
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "file_path_restriction",
      "parameters": { "restricted_file_paths": [".github/workflows/floor.yml"] } }
  ]
}
JSON

# 3. Create the anchor read token secret (fine-grained PAT, Administration: read).
gh secret set FLOOR_ANCHOR_TOKEN --repo "$REPO"   # paste the PAT when prompted
```

## Honest-degrade note (E2)

**Descope decision (maintainer, 2026-07-10).** GitHub structurally refuses push
rulesets on public, user-owned repos, so the `floor.yml` path lock (step 2, an
active push ruleset with a `file_path_restriction` rule) **cannot be created on
this repo**. Observed evidence from attempting the ruleset creation:

```
HTTP 422
"Source public repos cannot have push rules"
"Source only org-owned repos can have push rules"
```

Rather than migrate the repo into an organization solely to gain this rule, the
maintainer chose (per PRD E2) to **descope the floor.yml path lock**. The gap is
handled as a *named, documented capability limit*, not shipped as if the lock
were live:

- The two hard requirements remain **fail-closed**: both `floor enforcement` and
  `floor self-anchor` must be required status checks on the default branch, and
  branch protection must be readable via `FLOOR_ANCHOR_TOKEN`. The
  `floor self-anchor` job still fails the PR if either does not hold.
- The path-restriction check is **downgraded to a loud warning** (stderr
  `::warning::` + job summary) that quotes the 422 evidence and this descope
  decision. It is keyed on the explicit `PATH_LOCK_DESCOPED` constant in
  `scripts/floor_anchor.py`, not silently removed.

**Residual risk (stated honestly).** Without the path lock, a `floor.yml`-gut
attack is *detected only until a gutted workflow merges* - the deterministic
marker/clause checks catch the edit in the PR, but nothing structurally blocks a
self-merge of a workflow that removes its own enforcement. Prevention now rests
on **process signals, named as such**: the required status checks (`floor
enforcement` + `floor self-anchor`), code review, and the retro boundary
(FLOOR.md clause iii's out-of-band maintainer sign-off). These are process
controls, not the mechanical file-path lock they replace.

The push-ruleset creation attempt (step 2 in Configuration commands) was
observed to fail on 2026-07-10 with:

```
HTTP 422
"Source public repos cannot have push rules"
"Source only org-owned repos can have push rules"
```

That evidence is what grounds this descope; it is the same failure quoted above.
