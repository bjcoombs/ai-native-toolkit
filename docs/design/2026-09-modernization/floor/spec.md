# WS0 spec: Floor

## Intent

Briefs R0 and R19 of the programme described in [intent.md](../intent.md) (index: [README.md](../README.md)), parsed into Task Master tag `modernization-floor`: make the floor see a rename, give clause iii a named sign-off artefact, and land the six split-independent CI guards.

## Current state

Every number below was measured against this checkout at `44bddbf`, the commit `main` carried when the spec was written. Each block shows the command and its real output.

```
$ git rev-parse HEAD
44bddbfa68d70685be3d6691bc17445ab98386ed
```

### The marked set is a hard-coded constant

`scripts/floor_check.py` is 240 lines / 9,764 bytes and holds the marked set, the token list and the clause table as module constants.

```
$ rg -n 'MARKED_FILES|FLOOR_TOKENS|^MARKER =|^INVOCATIONS =|^FLOOR_FILE =|^REQUIRED_CLAUSES' scripts/floor_check.py
52:MARKER = "<!-- floor:cold-verify-completion -->"
53:INVOCATIONS = ("start_gate.py", "spawn_verifier.py", "complete_gate.py")
54:FLOOR_TOKENS = (MARKER, *INVOCATIONS)
58:MARKED_FILES = (
67:FLOOR_FILE = "FLOOR.md"
68:REQUIRED_CLAUSES = {
94:    tokens=FLOOR_TOKENS,
169:    files = args.files or list(MARKED_FILES)
184:    carried = [t for t in FLOOR_TOKENS if base_text and t in base_text]
```

`MARKED_FILES` holds four entries: `skills/marathon/SKILL.md`, `skills/pr-review-merge/SKILL.md`, `commands/tm.md`, `commands/issues.md` (`scripts/floor_check.py:58-63`). `FLOOR_TOKENS` holds four tokens: the marker plus three gate invocations (`:52-54`). `REQUIRED_CLAUSES` holds four clause ids, each an anchor comment plus a key phrase (`:68-73`).

### Token discovery at a ref returns six candidates, four after the anchor filter

```
$ git grep -l -F '<!-- floor:cold-verify-completion -->' HEAD -- ':!FLOOR.md'
HEAD:commands/issues.md
HEAD:commands/tm.md
HEAD:docs/floor-anchor-proof.md
HEAD:scripts/floor_check.py
HEAD:skills/marathon/SKILL.md
HEAD:skills/pr-review-merge/SKILL.md
```

Two of the six carry the marker only as prose or as a source constant. Filtering on a standalone anchor line, which is the definition `standalone_anchor_count` already uses at `scripts/floor_check.py:76-88`, reduces the six to exactly today's four:

```
$ for f in $(git grep -l -F '<!-- floor:cold-verify-completion -->' HEAD -- ':!FLOOR.md' | sed 's/^HEAD://'); do
    n=$(git show "HEAD:$f" | grep -c -x '[[:space:]]*<!-- floor:cold-verify-completion -->[[:space:]]*')
    t=$(git show "HEAD:$f" | grep -c -F '<!-- floor:cold-verify-completion -->')
    echo "$f standalone=$n total=$t"
  done
commands/issues.md standalone=1 total=2
commands/tm.md standalone=1 total=2
docs/floor-anchor-proof.md standalone=0 total=5
scripts/floor_check.py standalone=0 total=1
skills/marathon/SKILL.md standalone=1 total=2
skills/pr-review-merge/SKILL.md standalone=1 total=1
```

### Rename detection, simulated on a scratch clone

A bare `git mv` of all four marked files into `plugins/delivery/skills/<name>/SKILL.md`, committed alone, maps every one at 100 percent similarity:

```
$ git diff -M50% --name-status --diff-filter=R HEAD~1 HEAD
R100    commands/issues.md      plugins/delivery/skills/issues/SKILL.md
R100    skills/marathon/SKILL.md        plugins/delivery/skills/marathon/SKILL.md
R100    skills/pr-review-merge/SKILL.md plugins/delivery/skills/pr-review-merge/SKILL.md
R100    commands/tm.md  plugins/delivery/skills/tm/SKILL.md
```

The same move with the standalone anchor line stripped in the same commit still maps, at R099, so the pair stays comparable and the removed token is visible on the mapped pair:

```
$ git diff -M50% --name-status HEAD~1 HEAD | grep -i marathon
R099    skills/marathon/SKILL.md        plugins/delivery/skills/marathon/SKILL.md
```

A "move" that also truncates the file below the similarity threshold falls out of rename detection entirely and reads as a deletion, which today's removal detection already fails:

```
$ git mv skills/marathon/SKILL.md docs/archive/SKILL.md && head -100 docs/archive/SKILL.md > … && git diff -M50% --name-status HEAD~1 HEAD
A       docs/archive/SKILL.md
D       skills/marathon/SKILL.md
```

### Three more places hard-code the same paths

`.github/workflows/floor.yml` is 202 lines / 9,849 bytes. Its canary path filter carries an eight-alternative regex:

```
$ rg -n 'grep -qE' .github/workflows/floor.yml
142:            | grep -qE '^(skills/marathon/|skills/pr-review-merge/|commands/tm\.md$|commands/issues\.md$|scripts/contract/|scripts/canaries/|tests/canaries/|\.github/workflows/floor\.yml$)'; then
```

Those eight alternatives protect three whole directories and cover 38 files in the tree at that commit:

```
$ git ls-tree -r --name-only 44bddbf | grep -cE '^(skills/marathon/|skills/pr-review-merge/|commands/tm\.md$|commands/issues\.md$|scripts/contract/|scripts/canaries/|tests/canaries/|\.github/workflows/floor\.yml$)'
38
```

`FLOOR.md` is 58 lines / 2,947 bytes and names paths in two sections:

```
$ rg -n 'skills/marathon|skills/pr-review-merge|commands/tm|commands/issues|scripts/contract|scripts/canaries|tests/canaries|floor\.yml' FLOOR.md
9:The floor is enforced mechanically. `.github/workflows/floor.yml` is a required
36:gate invocations, `.github/workflows/floor.yml`, `scripts/contract/`,
37:`scripts/canaries/`, or `tests/canaries/` takes effect only with the
52:It appears in `skills/marathon/SKILL.md`, `skills/pr-review-merge/SKILL.md`,
53:`commands/tm.md`, and `commands/issues.md`. Alongside it, those files carry the
55:`complete_gate.py`. `.github/workflows/floor.yml` fails any PR that removes a
```

Clause iii (`FLOOR.md:33-38`) names an approval with no artefact: "takes effect only with the maintainer's explicit, out-of-band approval". Neither `scripts/floor_check.py` nor `scripts/floor_anchor.py` appears in its protected list.

A pytest regression guard reads one marked file by literal path:

```
$ sed -n '121,127p' scripts/tests/test_floor_check.py
def test_real_marathon_anchor_removal_is_flagged():
    # Regression guard against the real file: the marathon skill carries the
    # standalone anchor AND a prose mention (Retro Boundary section). Simulate
    # Attack A -- drop only the standalone anchor line -- and require a flag.
    repo_root = Path(__file__).resolve().parents[2]
    marathon = repo_root / "skills" / "marathon" / "SKILL.md"
    base = marathon.read_text(encoding="utf-8")
```

`scripts/tests/test_floor_check.py` is 248 lines and holds 27 test functions.

### Repo settings

`scripts/floor_anchor.py` is 345 lines / 15,534 bytes. It fails closed on two requirements: both floor contexts required on the default branch, and branch protection readable at all. Six contexts are required today, and each is produced by a job `name:` literal in this repo's workflows:

```
$ gh api repos/bjcoombs/ai-native-toolkit/branches/main/protection/required_status_checks | jq '[.checks[].context]'
["skills/assess pytest","scripts/ pytest","plugin contract pytest","Validate PR title","floor enforcement","floor self-anchor"]

$ rg -n '^\s{4}name: ' .github/workflows/*.yml | sed 's/:.*name: /  ->  /'
.github/workflows/assess-gate.yml  ->  AI-readiness regression gate
.github/workflows/tests.yml  ->  skills/assess pytest
.github/workflows/tests.yml  ->  scripts/ pytest
.github/workflows/tests.yml  ->  plugin contract pytest
.github/workflows/tests.yml  ->  ruff + mypy gates
.github/workflows/pr-lint.yml  ->  Validate PR title
.github/workflows/pr-lint.yml  ->  Auto-label from PR title
.github/workflows/floor.yml  ->  floor enforcement
.github/workflows/floor.yml  ->  floor self-anchor
.github/workflows/floor.yml  ->  canary path filter
.github/workflows/floor.yml  ->  canary suite (semantic layer)
```

One environment exists, and it is not a floor artefact:

```
$ gh api repos/bjcoombs/ai-native-toolkit/environments | jq '{total_count, names: [.environments[].name]}'
{"total_count": 1, "names": ["github-pages"]}
```

The token-rotation instructions name no app scope, so a rotated secret never reaches the Dependabot copy:

```
$ rg -n 'gh secret set' scripts/floor_anchor.py docs/floor-anchor-proof.md
scripts/floor_anchor.py:106:     gh secret set FLOOR_ANCHOR_TOKEN --repo "{repo}"   # paste a PAT: Administration: read
docs/floor-anchor-proof.md:178:gh secret set FLOOR_ANCHOR_TOKEN --repo "$REPO"   # paste the PAT when prompted
```

### The R19 guard gaps

```
$ rg -c '^def test_' skills/assess/tests/test_action_contract.py
14
$ rg -c 'working-directory' skills/assess/tests/test_action_contract.py || echo "0 assertions"
0 assertions
$ rg -n 'working-directory' action.yml
89:      working-directory: ${{ github.action_path }}/skills/assess
113:      working-directory: ${{ github.action_path }}/skills/assess
$ rg -n 'steps.render' .github/workflows/assess-gate.yml || echo "no steps.render reference"
no steps.render reference
$ rg -n '^outputs:|id: render|continue-on-error' action.yml
81:      id: render
86:      continue-on-error: true
$ rg -n 'fetch-depth' .github/workflows/tests.yml || echo "no fetch-depth in tests.yml"
no fetch-depth in tests.yml
$ rg -n 'command_files|rglob' tests/test_plugin_contract.py
75:def command_files():
80:    return [d / "SKILL.md" for d in skill_dirs()] + command_files()
94:    for p in sorted(REPO.rglob("*.md")):
155:@pytest.mark.parametrize("p", command_files(), ids=lambda p: p.name)
```

`test_subagent_types_resolve` is parametrized over `command_files()` (`tests/test_plugin_contract.py:155-156`), so it yields zero cases once `commands/` is gone. `action.yml` has no `outputs:` block, so `assess-gate.yml` cannot read the render outcome. The `plugin contract pytest` job checks out at the default fetch depth with no base fetch, so no test in it can compare against the merge-base.

## Design

### Inherited decisions

D8 fixes the shape: PR0 (floor only) then PR1 (the move), no staging. D7 keeps `action.yml` at the repo root and every CI job `name:` literal unchanged, which is why PR0 adds jobs but renames none. D1 and D2 fix the destination paths and the version line that PR1 moves onto, and are the reason the floor must become path-agnostic rather than gain a second hard-coded list. D3 puts this spec here. D4, D5, D6 and D9 bear on later workstreams and change nothing in WS0.

### Token discovery replaces the constant

`MARKED_FILES` is deleted. `floor_check.py markers --base $BASE_SHA` discovers the marked set by running `git grep -l -F <marker> $BASE_SHA -- ':!FLOOR.md'` and keeping the files whose content at `$BASE_SHA` has a standalone anchor line. The measurement above shows this yields exactly today's four files and excludes the two incidental carriers. `FLOOR.md` is excluded because it defines the token.

`FLOOR_TOKENS` stops being a module constant. The tokens are read from a fenced block that `FLOOR.md` declares, fetched with `git show $BASE_SHA:FLOOR.md`. The `clauses` subcommand fails if that block is missing or if it carries fewer tokens than the constant it replaces, so shrinking the token list is a floor change and not a refactor.

### Rename mapping

The head set is mapped from the base set with `git diff -M50% --name-status --diff-filter=R $BASE_SHA`. For a mapped pair the comparison is `BASE:old` against `HEAD:new`, so a byte-identical move passes with zero floor edits (measured: R100 for all four). A move that also weakens a token stays mapped while similarity holds (measured: R099 with the anchor line stripped) and fails on the token comparison; a move that rewrites past the threshold leaves rename detection entirely and fails as a deletion (measured: `D` plus `A`). Both paths are red, which is the point: PR1 is a pure move and the floor now enforces purity mechanically.

A structural check rejects a mapped destination that is not a plausible component path: the new path must match `skills/<x>/SKILL.md`, `plugins/<p>/skills/<x>/SKILL.md`, or `commands/<x>.md`. A "move" into `docs/archive/` is a deletion wearing a rename.

Rejected: raising the similarity threshold to `-M100%`, which would make any whitespace change during a move read as a deletion and hand PR1 an unfixable red; and passing `--find-renames` over the whole diff without a structural check, which accepts an archive path as a live one.

### `protected --base --changed` replaces the path regex

A new subcommand, `floor_check.py protected --base $BASE_SHA --changed`, reads changed paths on stdin or from `git diff --name-only` and decides protection by directory role rather than by literal path. Four roles, each a whole subtree rather than a basename allowlist:

- **Marked component.** Any path under the component directory of a file carrying the floor marker at `$BASE_SHA` or at `HEAD`. For a marked `skills/<x>/SKILL.md` or `plugins/<p>/skills/<x>/SKILL.md` the component is the parent directory and everything beneath it; for a marked `commands/<x>.md` the component is that file. This keeps `skills/marathon/forge/` protected, as today's `skills/marathon/` prefix does.
- **Gate code.** The whole of `scripts/contract/` - all ten files.
- **Canary code and fixtures.** The whole of `scripts/canaries/` and `tests/canaries/`.
- **Floor core.** `FLOOR.md`, `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`.

The first three roles plus `floor.yml` reproduce the eight-alternative regex at `.github/workflows/floor.yml:142` exactly over the tree at `44bddbf` - the same 38 files measured above. The floor core role widens that by exactly three files the regex omits today, `FLOOR.md`, `scripts/floor_check.py` and `scripts/floor_anchor.py`, which the sign-off job needs. Requirement 8 states the diff that proves both halves.

The regex is replaced by a call to the subcommand, and `FLOOR.md` is rewritten so no section names a path PR1 moves: the Markers section describes the token and points at the discovery command, and clause iii names only the floor core, the gate code and the canary fixtures, none of which move.

Rejected: matching gate scripts by basename (`start_gate.py`, `spawn_verifier.py`, `complete_gate.py`, `run_canaries.py`), which drops eight currently-protected files - the seven files in `scripts/contract/` that are not one of those basenames, plus `scripts/canaries/drive_interactive.mjs`. Under that narrowing an edit to `scripts/contract/verifier.py`, the verifier the canary suite exercises, would trigger neither the canary suite nor the sign-off job.

### Sign-off artefact

Clause iii's "out-of-band sign-off" gets one artefact: a `floor-signoff` GitHub Environment with the repository owner as its sole required reviewer. The measurement above confirms no such environment exists yet, so PR0 creates it. `floor.yml` gains a `floor sign-off` job, job id `signoff`, carrying `environment: floor-signoff` and running only when `protected` reports a floor core file or a token change. `floor enforcement` gains `needs: [signoff]` and, with it, the guard `if: ${{ !cancelled() && needs.signoff.result != 'failure' }}`.

The guard is not optional. GitHub propagates a skipped `needs` job to its dependents, and branch protection reads a skipped required context as satisfied - so an unguarded `needs` would let any PR that touches nothing protected skip both jobs and go green with removal detection and clause integrity never having run. That is the invariant `.github/workflows/floor.yml:115-116` already records: floor.yml must keep running on all PRs for the two deterministic jobs. With the guard, `floor enforcement` executes on both success and skip of `floor sign-off`, and is blocked only when the maintainer's deployment review is refused. The job id carries no hyphen so `needs.signoff.result` parses as property access rather than subtraction.

Merge therefore stays blocked by the two floor contexts that branch protection already requires, so PR0 needs no branch-protection PATCH and adds no required context. The approval is a separate click in the deployments UI with an actor and a timestamp GitHub records outside the diff.

`floor_anchor.py` gains a fail-closed assertion: the `floor-signoff` environment exists, the owner is a required reviewer, and the checked-out `floor.yml` still wires `environment: floor-signoff` into a job that `floor enforcement` needs. Any inability to confirm those is a failure, matching the two assertions the script already fails closed on.

`FLOOR.md` clause iii gains the sentence "recorded as the maintainer's deployment review of the floor-signoff environment", `REQUIRED_CLAUSES['iii']` gains the key phrase `floor-signoff`, and clause iii's protected set gains `scripts/floor_check.py` and `scripts/floor_anchor.py`, which it omits today. Naming those by path is safe and naming the marked files by path is not: the floor core, `scripts/contract/`, `scripts/canaries/` and `tests/canaries/` all stay where they are, while `skills/`, `commands/` and `plugins/` are exactly what PR1 rewrites.

Rejected artefacts, each because it sits inside the thing it is meant to approve or proves nothing: a signed-commit trailer (PR head commits here are unsigned and every squash is web-flow-signed, so the check is vacuous, and the trailer is in the diff); a `.floor/signoffs/` file (in the diff); a PR comment (needs a write token in a workflow that holds `contents: read`).

### The six R19 guards

Each is worth landing even if the split were cancelled, and each is small enough to review inside PR0.

1. `test_action_contract.py` asserts every `working-directory` in `action.yml` resolves on disk, closing the gap the 14 existing tests leave.
2. `action.yml` gains `outputs.rendered` sourced from the `render` step; `assess-gate.yml` gives its `uses: ./` step an `id` and asserts `steps.<id>.outputs.rendered != 'failure'` for this repo's own self-test. Reading `steps.render.outcome` from the caller does not work: `render` is a step inside the composite (`action.yml:81`), so the name is not in the caller's scope, and the composite step's own `outcome` is always `success` because the inner step carries `continue-on-error: true` (`action.yml:86`). Asserting equality with `success` does not work either: `render` is skipped rather than failed when uv is unavailable (`action.yml:87`), so a transient install failure would turn the gate red against the warn-only contract documented at `action.yml:82-86`. Consumers stay warn-only, because the assertion lives in the workflow, not the composite.
3. `floor_anchor.py` asserts every required status-check context is produced by a job `name:` in `.github/workflows/*.yml` on this branch: a subset check over the eleven literals measured above, so a job rename that orphans a required context goes red in the PR that renames it rather than after merge.
4. The `plugin contract pytest` job gains `fetch-depth: 0` and an explicit base fetch, which is the precondition for the R10 same-PR bump test and the R15 changelog test in WS7.
5. The rotation text in `scripts/floor_anchor.py:106` and `docs/floor-anchor-proof.md:178` gains `--app dependabot` alongside the default, so a rotated `FLOOR_ANCHOR_TOKEN` reaches the Dependabot secret store; today a rotation leaves every Dependabot PR red.
6. `tests/test_plugin_contract.py` discovers components with `rglob` and re-parametrizes `test_subagent_types_resolve` over the existing `shipped_md()` helper (`tests/test_plugin_contract.py:79-80`, skill `SKILL.md` files plus `commands/*.md`), which the sibling `test_use_the_skill_references_resolve` already uses at `:148`. Parametrizing over `SKILL.md` alone would drop `commands/tm.md`, the only command carrying `subagent_type` references, in PR0 - before PR1 has moved anything.

## Requirements

1. `MARKED_FILES` is absent from `scripts/floor_check.py`; `rg -c 'MARKED_FILES' scripts/floor_check.py` exits non-zero.
2. `floor_check.py markers --base <ref>` discovers its file set by token at `<ref>` and keeps only files with a standalone anchor line there. On `main` at `44bddbf` the discovered set is exactly the four files listed in Current state.
3. Floor tokens are read from a fenced block in `git show <ref>:FLOOR.md`. `floor_check.py clauses` fails when that block is absent or carries fewer than four tokens.
4. A bare rename of a marked file, mapped by `git diff -M50% --name-status --diff-filter=R`, compares `BASE:old` against `HEAD:new` and passes with no floor edit.
5. A mapped rename that weakens a token fails, and an unmapped disappearance of a marked file fails.
6. A rename whose destination does not match `skills/<x>/SKILL.md`, `plugins/<p>/skills/<x>/SKILL.md`, or `commands/<x>.md` fails as a deletion.
7. `floor_check.py protected --base <ref> --changed` classifies a changed-path list by directory role and is the only path decision `.github/workflows/floor.yml` makes; no path literal from the current regex survives in the workflow.
8. The role classification loses nothing the regex protects. Over the tree at `44bddbf`, `protected` returns the 38 files the regex matches plus exactly three more - `FLOOR.md`, `scripts/floor_anchor.py`, `scripts/floor_check.py` - and drops none of them:

   ```
   git ls-tree -r --name-only 44bddbf | python scripts/floor_check.py protected --base 44bddbf --changed | sort > /tmp/roles.txt
   git ls-tree -r --name-only 44bddbf | grep -E '^(skills/marathon/|skills/pr-review-merge/|commands/tm\.md$|commands/issues\.md$|scripts/contract/|scripts/canaries/|tests/canaries/|\.github/workflows/floor\.yml$)' | sort > /tmp/regex.txt
   diff /tmp/regex.txt /tmp/roles.txt
   ```

   The diff carries three `>` lines, naming those three files, and no `<` line. In particular `scripts/contract/verifier.py`, `scripts/contract/tiers.py` and `scripts/canaries/drive_interactive.mjs` are classified `protected`.
9. `FLOOR.md` names no path that PR1 moves: `rg -n 'skills/|commands/|plugins/' FLOOR.md` returns no lines and exits non-zero. The Markers section describes the token and points at the discovery command instead of listing the four marked files.
10. A `floor-signoff` environment exists on the repository with the owner as sole required reviewer, and `gh api repos/bjcoombs/ai-native-toolkit/environments` lists it.
11. `.github/workflows/floor.yml` has a `floor sign-off` job, job id `signoff`, with `environment: floor-signoff`, and `floor enforcement` declares `signoff` in `needs`. No job `name:` literal changes and no new required status check is registered.
12. `floor enforcement` still executes on a PR that touches nothing protected. The job carries `if: ${{ !cancelled() && needs.signoff.result != 'failure' }}`, so a skipped `floor sign-off` does not propagate. Verifying observation on such a PR: `gh api repos/bjcoombs/ai-native-toolkit/actions/runs/<run-id>/jobs | jq '[.jobs[] | {name, conclusion}]'` shows `floor sign-off` with conclusion `skipped` and `floor enforcement` with conclusion `success` - the job ran, it was not skipped - and the `floor enforcement` log carries the `merge-base:` line its removal-detection step prints.
13. `scripts/floor_anchor.py` fails closed when the environment is absent, when the owner is not a required reviewer, or when the checked-out `floor.yml` no longer wires the environment into a `needs` of `floor enforcement`.
14. `FLOOR.md` clause iii carries the phrase `floor-signoff` and names the six protected paths that PR1 does not move - `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, `scripts/contract/`, `scripts/canaries/`, `tests/canaries/`; `REQUIRED_CLAUSES['iii']` asserts the phrase. `rg -n 'floor_check\.py|floor_anchor\.py|floor\.yml|scripts/contract/|scripts/canaries/|tests/canaries/' FLOOR.md` lists all six, and requirement 9's command still returns nothing. Requirement 9 and this one do not overlap: the paths PR1 moves are forbidden, the paths that stay are required.
15. `scripts/tests/test_floor_check.py:125-126` is re-pointed at a file resolved through discovery rather than the literal `skills/marathon/SKILL.md`.
16. `test_action_contract.py` asserts each `working-directory` value in `action.yml` exists on disk; both current values (`action.yml:89` and `:113`) pass.
17. `action.yml` declares `outputs.rendered` sourced from the `render` step, `.github/workflows/assess-gate.yml` gives its `uses: ./` step an `id`, and the assertion fails only on `steps.<id>.outputs.rendered == 'failure'`. A render skipped for want of uv leaves the job green.
18. `scripts/floor_anchor.py` asserts every required context is a job `name:` literal in `.github/workflows/*.yml`; the six contexts measured above pass the subset check.
19. The `plugin contract pytest` job in `.github/workflows/tests.yml` sets `fetch-depth: 0` and fetches the PR base ref.
20. `scripts/floor_anchor.py:106` and `docs/floor-anchor-proof.md:178` both carry a `--app dependabot` rotation line beside the default one.
21. `tests/test_plugin_contract.py` discovers components with `rglob`, and `test_subagent_types_resolve` is parametrized over `shipped_md()` rather than `command_files()`, so `commands/tm.md` keeps its coverage in PR0 and no case is lost when `commands/` moves. Collecting it yields 20 cases on `main` at `44bddbf`, the same count `test_use_the_skill_references_resolve` already collects.

## Verification

- **`scripts/ pytest`** (required context) covers requirements 1-6, 8 and 15. New cases in `scripts/tests/test_floor_check.py`: discovery returns the four-file set from a fixture repository whose sixth candidate carries only a prose mention; the R100 pair passes; the R099 anchor-strip pair fails; the truncated `D`/`A` pair fails; the `docs/archive/` destination fails the structural check; a `FLOOR.md` whose token block is deleted fails `clauses`; and requirement 8's set comparison runs against the real tree at `44bddbf`, asserting the three-line delta and no dropped file, so a later narrowing of the roles goes red here.
- **`plugin contract pytest`** (required context) covers requirements 19 and 21 through `tests/test_plugin_contract.py`, including a collection-count assertion so `test_subagent_types_resolve` cannot regress to zero cases.
- **`skills/assess pytest`** (required context) covers requirement 16 through the fifteenth test in `skills/assess/tests/test_action_contract.py`.
- **`floor enforcement`** (required context) runs the rewritten `markers` and `clauses` subcommands plus the new `protected` call against PR0 itself. PR0 touches the floor core, so its own run cannot demonstrate requirement 12; that observation comes from a nothing-protected PR instead.
- **`floor self-anchor`** (required context) covers requirements 13 and 18. Its first run on PR0 is expected to fail closed on the environment assertion until the environment is created, which is the fail-closed behaviour the script already has for branch protection; the red is the signal that the out-of-band step is outstanding.
- **`canary suite (semantic layer)`** runs on PR0 because the diff touches `.github/workflows/floor.yml` and the floor core, driving `scripts/canaries/run_canaries.py` against the committed and per-run blind fixtures. See [floor-anchor-proof.md](../../../floor-anchor-proof.md) for the anchor layer's evidence format.
- **`AI-readiness regression gate`** covers requirement 17 end to end: after the change, a deliberately broken `working-directory` in a scratch branch turns the job red instead of emitting a notice.
- **Recorded verdicts** cover requirements 10, 11 and 12: `gh api repos/bjcoombs/ai-native-toolkit/environments` and the deployment review record for PR0 are pasted into the PR body. Requirement 11's "no new required check" is confirmed by re-running the `required_status_checks` query in Current state and showing the same six contexts. Requirement 12 is confirmed on a throwaway docs-only branch opened against PR0's head: its Floor run's jobs listing shows `floor sign-off` skipped and `floor enforcement` succeeded, and that listing is pasted into PR0's body beside the others.

## Breadcrumbs

WS0 leaves no shim and no forwarding alias, and it adds no row to `docs/migration-2.0.md`, which does not exist until WS1 creates it. Nothing user-facing changes: no skill, agent, command, manifest or plugin version is touched, so no `remove-in` marker is needed and no version bump applies.

One Deprecation Path row in [intent.md](../intent.md) belongs to this workstream and its breadcrumb is permanent rather than removable: `MARKED_FILES` deleted (R0), breadcrumb "token discovery; `FLOOR.md` describes the token", removed in "never". The replacement for the constant is the `FLOOR.md` token block plus the discovery command, both of which stay.

## Rollback

Revert the PR0 merge commit with `git revert -m 1 <sha>` on a branch and merge that revert. `main` returns to green because every change PR0 makes is additive or internal to files no other workflow reads: `floor_check.py` returns to its `MARKED_FILES` constant, `floor.yml` returns to its path regex, and the four marked files are still at their pre-PR1 paths, so removal detection has the same file set either way. The revert PR touches floor core files, so it triggers the `floor sign-off` job introduced by the commit being reverted; approve it through the environment before merging, then delete the environment only if the revert is permanent.

The `floor-signoff` environment and the required-reviewer setting live in repo settings, not in the diff, so a revert leaves them in place. That is harmless: with the `floor sign-off` job gone, nothing references the environment. No tag, release, or published artefact is produced by PR0, so nothing immutable has to be un-shipped.

If only one guard misbehaves, prefer a forward fix: guards 1 to 6 are independent of the R0 rewrite and of each other, and each can be reverted on its own file without disturbing the discovery change.

## Sequencing

**PR0** is the single PR of this workstream, and the programme's single sign-off. It contains the R0 rewrite and all six R19 guards, and it approves itself through the environment it introduces: the maintainer creates the `floor-signoff` environment out-of-band, the `floor sign-off` job then requests a deployment review on PR0's own head, and the approval is recorded before `floor enforcement` runs.

PR0 may touch: `scripts/floor_check.py`, `scripts/floor_anchor.py`, `scripts/tests/test_floor_check.py`, `FLOOR.md`, `.github/workflows/floor.yml`, `.github/workflows/tests.yml`, `.github/workflows/assess-gate.yml`, `action.yml`, `skills/assess/tests/test_action_contract.py`, `tests/test_plugin_contract.py`, `docs/floor-anchor-proof.md`.

PR0 may not touch: any file under `skills/` other than the assess contract test, any file under `agents/`, `commands/`, `plugins/` or `.claude-plugin/`; any job `name:` literal (D7); branch protection; and the content of the four marked files, whose paths PR1 moves.

WS1 begins only after PR0 has merged and the required contexts are green on `main`. PR1 then makes the bare `git mv` as its first commit and must merge with zero edits to `floor_check.py`, `floor_anchor.py`, `floor.yml` or `FLOOR.md`; any such edit means this workstream did not finish its job.
