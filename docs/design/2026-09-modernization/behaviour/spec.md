# WS8 spec: Behaviour

## Intent

WS8 delivers brief R16 of the programme recorded in [intent.md](../intent.md), under Task Master tag `modernization-behaviour`; the workstream index is [README.md](../README.md).

## Current state

Measured at `44bddbf` on `main`. Every number below is quoted with the command that produced it and that command's real output.

### The huddle completeness-critic line shipped

`workflow-pattern-port` R3 asked for one bounded completeness-critic pass in `/huddle`, resolved gaps, and a coverage line in the verdict template. All three are in the skill body:

```console
$ rg -c 'completeness-critic pass' skills/huddle/SKILL.md
3
$ rg -n '^### Coverage' skills/huddle/SKILL.md
402:### Coverage
```

The Task Master record agrees. The tasks file lives outside the repository, in the Task Master project root beside the checkout, so the reading is carried into this spec as the durable record:

```console
$ jq -r '."workflow-pattern-port".tasks[] | select(.id==3) | "\(.id)  \(.status)  \(.title)"' .taskmaster/tasks/tasks.json
3  done  Completeness-critic pass for /huddle
```

What did not ship with it is a recorded `skill-forge` promotion verdict, and there is no huddle corpus to produce one from - the reason D9 makes an 8-case huddle transfer set a WS4 prerequisite:

```console
$ ls skills/huddle/
SKILL.md
```

### assess-pr self-feedback is create-only

Step 7 of `skills/assess-pr/SKILL.md` surfaces detected anomalies and, on explicit consent, files an issue. There is no search step and no reaction step:

```console
$ rg -n '^## Step 7' skills/assess-pr/SKILL.md
299:## Step 7: Tool Feedback (Optional)
$ rg -n 'gh issue create|--label assess-feedback|--repo bjcoombs' skills/assess-pr/SKILL.md
184:| GitHub Issues | `gh issue create --label assess-finding --title "..." --body "..."` |
319:gh issue create \
320:  --repo bjcoombs/ai-native-toolkit \
321:  --label assess-feedback \
```

No stable per-anomaly marker exists on either the instruction surface or the deterministic core, so a second run cannot find the issue a first run filed:

```console
$ rg -n 'assess-anomaly' skills/assess-pr/SKILL.md skills/assess/scripts/lib/anomaly_detector.py; echo "exit=$?"
exit=1
```

The match key the dedup needs already exists as five stable codes:

```console
$ rg -no 'code="[A-Z_]+"' skills/assess/scripts/lib/anomaly_detector.py
34:code="ZERO_FILES_SCORED"
41:code="ZERO_COMPLEXITY"
48:code="EMPTY_HOTSPOTS"
60:code="INSTRUCTION_FILE_GRADE_MISMATCH"
71:code="ALL_NEW_HOTSPOTS"
```

The consent surface that governs the offer names `feedback` twice:

```console
$ rg -c 'feedback' skills/assess/references/consent-lifecycle.md
2
```

### The six absorbed tasks

Tag `assess-feedback-upvote` has six pending tasks, all of which WS8 absorbs. The tasks file is outside the repository, so the titles are carried here as the durable record:

```console
$ jq -r '."assess-feedback-upvote".tasks[] | select(.status=="pending") | "\(.id)  \(.title)"' .taskmaster/tasks/tasks.json
1  Create feedback_marker.py helper module with marker derivation logic
2  Update assess-pr SKILL.md Step 7 to stamp new feedback issues with markers
3  Implement search-before-create dedup flow in Step 7
4  Add thumbs-up reaction to matched issues with idempotency check
5  Handle edge cases: closed issues, non-interactive runs, and gh unavailable
6  Update documentation (consent-lifecycle.md and SKILL.md references)
```

### 6hats is still a separate body

`6hats` is a command file, not a size argument, and `huddle` carries no `argument-hint`:

```console
$ rg -n 'name: 6hats' commands/6hats.md
2:name: 6hats
$ rg -n '6hats' skills/huddle/SKILL.md
519:- `/6hats database query performance issue` (alias for size 1 solo)
$ rg -n 'argument-hint' skills/huddle/SKILL.md; echo "exit=$?"
exit=1
```

### Gates available today

`skill-forge` ships its promotion machinery and two fixture documents; `marathon` is the only skill carrying a forge corpus:

```console
$ ls skills/skill-forge/tests/fixtures/
flawed-instruction-file
flawed-sample-skill
$ rg -no '^### [a-z]+-[0-9]+' skills/marathon/forge/corpus.md
8:### happy-1
32:### edge-1
45:### adv-1
59:### comp-1
73:### crash-1
```

So of the three candidates, two are gated on a corpus WS4 builds (D9) and one needs a corpus authored in its own PR.

## Design

Three candidates, three PRs, one recorded `skill-forge` promotion verdict each. R16 fixes the rule: `ab-equivalence` freezes a pure move, `skill-forge` gates an intended change. All three candidates are intended changes, so the artefact each PR records is the forge report's gate ledger showing Gate 1 and Gate 2 both passed, per [gate-hierarchy.md](../../../../skills/skill-forge/references/gate-hierarchy.md) and the sections of [forge-report-template.md](../../../../skills/skill-forge/references/forge-report-template.md).

### Candidate A: forge the huddle completeness-critic line

The line shipped as instruction text without a promotion verdict. This PR runs `skill-forge` over `skills/huddle/SKILL.md` with the critic pass and its coverage line as confirmed intent clauses, and lands only the amendments the panel's Gate 1 failures force. The expected diff is small or empty; the deliverable is the verdict.

Gate: promotion over the 8-case huddle transfer set built as the WS4 prerequisite (D9), with the critic-pass case among the cases that must pass Fidelity.

Rejected: re-implementing the critic pass because no verdict exists (the measurement above shows it shipped and behaves; a rewrite would be an unmeasured change, not a gate); gating on `ab-equivalence` (R16 assigns that to moves).

### Candidate B: assess-pr search-first self-feedback

This PR absorbs the six tasks listed above and implements `prd-assess-feedback-upvote.md` as written: a stable per-anomaly marker (an `anomaly:<code>` label plus an `<!-- assess-anomaly: <code> -->` body comment) stamped on the create path; a search of open `assess-feedback` issues by that marker before any create; an idempotent `+1` on a single match with a truthful count; a new issue referencing the closed number when the only match is closed; the existing consent gate, non-interactive skip, and honest-degrade paths unchanged in shape.

The split follows the existing one: deterministic marker derivation goes in `skills/assess/scripts/lib/feedback_marker.py` beside `anomaly_detector.py` with direct unit tests; the search, react, create, and consent orchestration stays in `skills/assess-pr/SKILL.md` Step 7, the instruction surface.

Gate: promotion over a six-case forge corpus authored in this PR at `skills/assess-pr/forge/corpus.md`, in the shape `skills/marathon/forge/corpus.md` already uses (happy, edge, adversarial, composition), covering match-found, no-match, already-reacted, closed-only match, non-interactive, and `gh` unavailable. Pytest covers the marker helper; the forge corpus covers the branch the instructions drive.

Rejected, cited from the absorbed source rather than re-argued: dedup for the target-repo findings issues (different repo, path-bearing match signature, different privacy posture); auto-filing without consent, and a `+1` comment per run (both re-introduce the noise the change removes); a maintainer-side frequency dashboard (the reaction count is the ranking).

### Candidate C: the huddle size argument replacing 6hats

D4 folds `6hats` into `huddle` as a size argument. R12 assigns the mechanical half to WS2, landing inside PR1: the `$0` size guard, the `argument-hint`, and the `6hats` stub kept under its old name with `remove-in: 3.0.0`. WS8 owns the behavioural half and nothing else: that `/huddle 1 <topic>` reaches the solo flat-parallel path the `6hats` body described, and that an explicit size argument overrides the chair's own sizing judgement rather than competing with it.

Gate: promotion over the same WS4 huddle transfer set, using its no-argument and size-1 gut-check cases, asserting the size-1 path is reachable by argument and not only by the chair's judgement.

Rejected: keeping `6hats` as a second skill body (D4 already settled the fold, and two bodies means two forge runs over one behaviour, which drift apart); moving the stub or the `argument-hint` into this PR (R12 puts them in PR1; duplicating them here creates a hot file across two workstreams).

### Rejected for the workstream

- One combined PR. Three unrelated forge verdicts in one diff means a Gate 1 failure on any candidate blocks the other two.
- Starting before WS4. The transfer set that gates two of the three candidates is a WS4 prerequisite (D9); without it there is no corpus to forge against.

## Requirements

1. `skills/huddle/SKILL.md` still carries the bounded completeness-critic pass and its coverage line after Candidate A: `rg -c 'completeness-critic pass' skills/huddle/SKILL.md` returns at least 1 and `rg -n '^### Coverage' skills/huddle/SKILL.md` returns a hit.
2. Candidate A's PR body carries the forge report gate ledger for a `skill-forge` run over `skills/huddle/SKILL.md` against the WS4 huddle transfer set, showing Gate 1 and Gate 2 passed. Recorded verdict.
3. `skills/assess/scripts/lib/feedback_marker.py` derives `anomaly:<code>` and `<!-- assess-anomaly: <code> -->` for each of the five codes in `anomaly_detector.py`; unit tests assert the derivation is stable per code and run under `uv run --with pytest pytest skills/assess/tests -q`.
4. Step 7 stamps both markers on every issue it creates: `rg -n 'assess-anomaly' skills/assess-pr/SKILL.md` returns a hit, where it returns none at `44bddbf`.
5. Step 7 searches open `assess-feedback` issues by `anomaly:<code>` before any create; on a single open match it adds exactly one `+1` reaction and creates no issue, and reports the issue URL and the resulting count. Forge corpus case `happy`.
6. A re-run where the current user has already reacted adds no second reaction and says so. Forge corpus case `edge`.
7. A closed-only match creates a new issue that references the closed issue number and does not reopen it. Forge corpus case `edge`.
8. A non-interactive run performs no search, no reaction, and no create, and records `{type: "feedback", status: "skipped", reason: "non-interactive"}` in `offers`, the behaviour `skills/assess/references/consent-lifecycle.md` already states. Existing interactive-gate tests.
9. With `gh` unavailable the flow prints the search URL and the anomaly code, exits without a traceback, and files nothing. Forge corpus case `adversarial`.
10. Every issue write, reaction or create, is preceded by an explicit confirmation naming the exact target and action; a decline writes nothing. Forge corpus case `adversarial`.
11. `skills/assess/references/consent-lifecycle.md` describes the dedup branch of the feedback offer alongside the create branch.
12. Candidate B's PR body carries the forge report gate ledger for a `skill-forge` run over `skills/assess-pr/SKILL.md` against `skills/assess-pr/forge/corpus.md`. Recorded verdict.
13. After Candidate C, an explicit size argument selects the huddle mode: the size-1 gut-check transfer-set case passes when the size arrives as an argument, with no change to the no-argument case's verdict.
14. Candidate C's PR body carries the forge report gate ledger for a `skill-forge` run over `skills/huddle/SKILL.md` against the WS4 huddle transfer set. Recorded verdict.
15. Each candidate PR bumps the version of the plugin it changes in the same diff, per D2 and R10.
16. `uv run --with pytest pytest tests/test_plugin_contract.py -q` is green on each candidate PR.

## Verification

| Candidate | Gate | Named set | Recorded artefact |
|-----------|------|-----------|-------------------|
| A: huddle critic line | `skill-forge` promotion | WS4 8-case huddle transfer set (D9), critic-pass case | Forge report gate ledger in the PR body |
| B: assess-pr search-first feedback | `skill-forge` promotion plus pytest | `skills/assess-pr/forge/corpus.md` (6 cases, authored in the PR); `skills/assess/tests` for `feedback_marker.py` | Forge report gate ledger in the PR body; green pytest run |
| C: huddle size argument | `skill-forge` promotion | WS4 8-case huddle transfer set (D9), no-argument and size-1 cases | Forge report gate ledger in the PR body |

Promotion means Gate 1 (every case passes Fidelity) and Gate 2 (no HIGH-severity dissent) both pass. A STOP verdict is not a merge: the candidate is reworked and re-forged, the same discipline R6 applies to a failed split. `ab-equivalence` is not the gate for any candidate here, because none of them is a move.

## Breadcrumbs

WS8 adds no shim and no `docs/migration-2.0.md` row of its own.

- The `6hats` stub and its `remove-in: 3.0.0` entry belong to R12 and WS2, and the migration row that maps `/6hats` to `/huddle` belongs to R18. Candidate C leaves both alone.
- `docs/migration-2.0.md` does not exist at `44bddbf` (`ls docs/migration-2.0.md` exits 1); R18 creates it, so WS8 has no file to add rows to.
- The forge corpus Candidate B authors is a permanent test asset, not a shim: it stays after 3.0.0 and feeds the `evals/` format WS9 defines under R9.

## Rollback

Each candidate is one PR against `main`, so each reverts independently with `git revert`.

- Candidate A: a revert restores the pre-forge `skills/huddle/SKILL.md`. The completeness-critic behaviour predates WS8, so a revert removes only the amendments, never the shipped R3 behaviour.
- Candidate B: a revert removes `skills/assess/scripts/lib/feedback_marker.py`, its tests, the Step 7 edits, and the forge corpus in one commit, returning Step 7 to its create-only flow. Issues already filed with an `anomaly:<code>` label survive the revert as inert labels; they carry no repo data and block nothing.
- Candidate C: a revert removes the behavioural amendments only. The `$0` guard, the `argument-hint`, and the `6hats` stub live in PR1 (R12), so `/6hats` and `/huddle` both keep working.
- No candidate touches `FLOOR.md`, a floor-marked file, a CI job `name:`, or `action.yml`, so no revert can change a required status-check context. Each revert restores green by restoring the previous commit exactly.

## Sequencing

WS8 starts after WS4 has merged, because the huddle transfer set is a WS4 prerequisite (D9) and gates candidates A and C. Candidate B depends on WS4 only for ordering, not for its corpus.

| PR | Candidate | May not touch |
|----|-----------|---------------|
| 1 | A: forge the huddle completeness-critic line | `skills/assess-pr/`, `skills/assess/`, the `6hats` stub, the huddle `argument-hint`, the transfer set itself |
| 2 | B: assess-pr search-first self-feedback | `skills/huddle/`, `skills/assess/scripts/` outside the new `lib/feedback_marker.py`, the deterministic scoring core, `run-context.json`'s schema |
| 3 | C: huddle size argument | `skills/assess-pr/`, the `6hats` stub, the huddle `argument-hint` and `$0` guard as landed by R12, `commands/` |

PRs 1 and 3 both edit `skills/huddle/SKILL.md`, so they run in that order rather than in parallel; PR 3 forges the file PR 1 promoted. PR 2 shares no file with either and can run alongside them. WS9 reads the outcome of all three (R9), so no candidate is left unmerged when WS9 starts.
