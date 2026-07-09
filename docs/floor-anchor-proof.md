# Floor Anchor Proof (PRD criterion 5 / E2)

The floor's markers and clause checks only bite while `.github/workflows/floor.yml`
is a **required** status check on the default branch and while `floor.yml` itself
is **path-restricted** against self-merge edits. This document records the proof
that both hold under the solo-merge setup, via two adversarial test PRs.

Both attack PRs are opened on throwaway branches, **never merged**, and closed
after the outcome is recorded. Neither branch is the feature branch.

## Repo settings under test

The enforcement topology this floor relies on (configured out-of-band by the
maintainer -- FLOOR.md clause iii):

1. **Required status check.** `floor enforcement` (the `floor.yml` job name) is a
   required status check on `main`, added alongside the existing required checks
   (`skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`,
   `Validate PR title`) without disturbing them.
2. **Path restriction.** An active push ruleset with a `file_path_restriction`
   rule covering `.github/workflows/floor.yml`, requiring maintainer bypass to
   modify that path.
3. **Anchor read token.** `FLOOR_ANCHOR_TOKEN` repo secret -- a fine-grained PAT
   with `Administration: read` -- so `floor.yml`'s self-anchor step can query the
   settings above. (The default `GITHUB_TOKEN` cannot read branch protection or
   rulesets.)

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
  -f 'checks[][context]=floor enforcement'

# 2. Path-restrict floor.yml via a push ruleset (maintainer bypass).
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

If GitHub's model cannot enforce the `floor.yml` path restriction on this
user-owned repo under solo-merge (e.g. push rulesets with `file_path_restriction`
are unavailable outside organizations), the gap is **not** shipped as
documentation: it returns to the maintainer for an explicit descope decision.
The outcome of the push-ruleset creation (step 2) is recorded in Attack B above.
