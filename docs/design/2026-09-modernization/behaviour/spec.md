# WS8 spec: Behaviour

## Intent

WS8 delivers brief R16 of the programme recorded in [intent.md](../intent.md), under Task Master tag `modernization-behaviour`; the workstream index is [README.md](../README.md).

`intent.md` holds the Problem Statement, Decisions, Deprecation Path, Programme sequence and Success Criteria. The R-briefs it cites by ID, R16 here and R4, R6, R9, R10, R12 and R18 below, live only in the programme PRD, an un-versioned Task Master working document kept outside the repository. R16 is carried in here as the durable record, the same treatment the Task Master task titles get below:

> **R16. Behaviour improvements gated by skill-forge**
>
> - Behaviour changes beyond R6's directive-clarity pass are in scope when they arrive with a `skill-forge` promotion verdict. `ab-equivalence` is the freeze for pure moves; `skill-forge` is the gate for intended change.
> - Candidates, each its own PR: `huddle`'s completeness-critic line (verify `workflow-pattern-port` R3 shipped); `assess-pr`'s search-first self-feedback (`prd-assess-feedback-upvote.md`, 6 pending tasks folded into WS8); the `huddle` size argument replacing `6hats`.

WS8 runs after WS4, so PR1's opening `git mv` has already landed (D1; intent.md, Programme sequence step 2). Every path in a code span from Design onward is therefore written at its post-PR1 location, `plugins/<family>/skills/<x>/`, taking the family from the programme's plugin table: `huddle` and its stub in `plugins/huddle/`, `assess`, `assess-findings` and `assess-pr` in `plugins/assess/`. Current state is measured before that move and keeps its pre-move paths, as do the markdown links, which resolve against the tree as it stands at `44bddbf`.

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

Three candidates, three PRs, one recorded `skill-forge` promotion verdict each. R16, quoted in Intent, fixes the rule: `ab-equivalence` freezes a pure move, `skill-forge` gates an intended change. All three candidates are intended changes, so the artefact each PR records is the forge report's gate ledger showing Gate 1 and Gate 2 both passed, per [gate-hierarchy.md](../../../../skills/skill-forge/references/gate-hierarchy.md) and the sections of [forge-report-template.md](../../../../skills/skill-forge/references/forge-report-template.md).

### Candidate A: forge the huddle completeness-critic line

The line shipped as instruction text without a promotion verdict. This PR runs `skill-forge` over `plugins/huddle/skills/huddle/SKILL.md` with the critic pass and its coverage line as confirmed intent clauses, and lands only the amendments the panel's Gate 1 failures force. The expected diff is small or empty; the deliverable is the verdict.

Gate: promotion over the 8-case huddle transfer set built as the WS4 prerequisite (D9). WS4 authors and enumerates that set in [disclosure/spec.md](../disclosure/spec.md), a placeholder until the WS4 spec PR lands; Verification below names the cases WS8 requires it to contain and the threshold they are judged against.

Rejected: re-implementing the critic pass because no verdict exists (the measurement above shows it shipped and behaves; a rewrite would be an unmeasured change, not a gate); gating on `ab-equivalence` (R16 assigns that to moves).

### Candidate B: assess-pr search-first self-feedback

This PR absorbs the six tasks listed above and implements `prd-assess-feedback-upvote.md` as written: a stable per-anomaly marker (an `anomaly:<code>` label plus an `<!-- assess-anomaly: <code> -->` body comment) stamped on the create path; a search of open `assess-feedback` issues by that marker before any create; an idempotent `+1` on a single match with a truthful count; a new issue referencing the closed number when the only match is closed; the existing consent gate, non-interactive skip, and honest-degrade paths unchanged in shape.

The split follows the existing one: deterministic marker derivation goes in `plugins/assess/skills/assess/scripts/lib/feedback_marker.py` beside `anomaly_detector.py` with direct unit tests; the search, react, create, and consent orchestration stays in `plugins/assess/skills/assess-pr/SKILL.md` Step 7, the instruction surface.

Step 7 reaches the module the way it already reaches the deterministic core, so the split lands no module without a caller. `skills/assess-pr/SKILL.md:270-275` resolves `SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"` and runs `uv run "$SKILL_DIR/scripts/assess_emit_workflow.py"`; the marker call is one more line in that block, `uv run "$SKILL_DIR/scripts/lib/feedback_marker.py" <code>`, and `feedback_marker.py` carries the `__main__` entry point `promissory_markers.py:482` and `accretion_ratchet.py:368` already use. Two consequences, stated here rather than left for the implementer:

- The reach is `${CLAUDE_PLUGIN_ROOT}`-shaped and cannot cross a plugin boundary, so `assess` and `assess-pr` must land in the same family. The programme's plugin table puts both in `plugins/assess/`, which is what makes this legal.
- R4 (WS3) rewrites these `SKILL_DIR` blocks to `${CLAUDE_SKILL_DIR}/scripts/<x>`, which resolves to the skill's own subdirectory. `skills/assess-pr/SKILL.md:271` is the only SKILL.md site in the tree pointing at another skill's directory (`rg -n 'CLAUDE_PLUGIN_ROOT' skills/*/SKILL.md` shows the other four naming their own), so it is the one site that needs a cross-skill form rather than a plain substitution. Candidate B does not make that change; it inherits whatever WS3 lands and asserts the invocation still resolves.

Gate: promotion over a six-case forge corpus authored in this PR at `plugins/assess/skills/assess-pr/forge/corpus.md`, in the shape `skills/marathon/forge/corpus.md` already uses and at the sidecar location [test-taxonomy.md](../../../../skills/skill-forge/references/test-taxonomy.md) requires (beside the document being forged, not inside `skill-forge`). One case per branch Step 7 can take, and the same six the Requirements verify:

| Case | Type | Branch | Requirement |
|------|------|--------|-------------|
| `happy-1` | happy path | No open match, consent given: one issue created carrying both markers | 4 |
| `happy-2` | happy path | One open match, consent given: exactly one `+1` reaction, no issue created | 5 |
| `edge-1` | edge | Open match the current user has already reacted to | 6 |
| `edge-2` | edge | The only match is closed | 7 |
| `adv-1` | adversarial | `gh` unavailable | 9 |
| `adv-2` | adversarial | Consent declined at the confirmation | 10 |

A non-interactive run is not a corpus case: it never reaches the offer, and the existing interactive-gate tests already assert the skip (Req 8). Pytest covers the marker helper; the forge corpus covers the branches the instructions drive.

Rejected, cited from the absorbed source rather than re-argued: dedup for the target-repo findings issues (different repo, path-bearing match signature, different privacy posture); auto-filing without consent, and a `+1` comment per run (both re-introduce the noise the change removes); a maintainer-side frequency dashboard (the reaction count is the ranking).

### Candidate C: the huddle size argument replacing 6hats

D4 folds `6hats` into `huddle` as a size argument. R12 assigns the mechanical half to WS2, landing inside PR1: the `$0` size guard, the `argument-hint`, and the `6hats` stub kept under its old name with `remove-in: 3.0.0`. WS8 owns the behavioural half and nothing else: that `/huddle 1 <topic>` reaches the solo flat-parallel path the `6hats` body described, and that an explicit size argument overrides the chair's own sizing judgement rather than competing with it.

Gate: promotion over the same WS4 huddle transfer set, using its no-argument and size-1 gut-check cases, asserting the size-1 path is reachable by argument and not only by the chair's judgement.

Rejected: keeping `6hats` as a second skill body (D4 already settled the fold, and two bodies means two forge runs over one behaviour, which drift apart); moving the stub or the `argument-hint` into this PR (R12 puts them in PR1; duplicating them here creates a hot file across two workstreams).

### Rejected for the workstream

- One combined PR. Three unrelated forge verdicts in one diff means a Gate 1 failure on any candidate blocks the other two.
- Starting before WS4. The transfer set that gates two of the three candidates is a WS4 prerequisite (D9); without it there is no corpus to forge against.

## Requirements

1. `plugins/huddle/skills/huddle/SKILL.md` still carries the bounded completeness-critic pass and its coverage line after Candidate A: `rg -c 'completeness-critic pass' plugins/huddle/skills/huddle/SKILL.md` returns at least 1 and `rg -n '^### Coverage' plugins/huddle/skills/huddle/SKILL.md` returns a hit.
2. Candidate A's PR body carries the forge report gate ledger for a `skill-forge` run over `plugins/huddle/skills/huddle/SKILL.md` against the WS4 huddle transfer set, showing Gate 1 and Gate 2 passed. Recorded verdict.
3. `plugins/assess/skills/assess/scripts/lib/feedback_marker.py` derives `anomaly:<code>` and `<!-- assess-anomaly: <code> -->` for each of the five codes in `anomaly_detector.py`; unit tests assert the derivation is stable per code and run under `uv run --with pytest pytest plugins/assess/skills/assess/tests -q`.
4. Step 7 stamps both markers on every issue it creates: `rg -n 'assess-anomaly' plugins/assess/skills/assess-pr/SKILL.md` returns a hit, where it returns none at `44bddbf`. Forge corpus case `happy-1`.
5. Step 7 searches open `assess-feedback` issues by `anomaly:<code>` before any create; on a single open match it adds exactly one `+1` reaction and creates no issue, and reports the issue URL and the resulting count. Forge corpus case `happy-2`.
6. A re-run where the current user has already reacted adds no second reaction and says so. Forge corpus case `edge-1`.
7. A closed-only match creates a new issue that references the closed issue number and does not reopen it. Forge corpus case `edge-2`.
8. A non-interactive run performs no search, no reaction, and no create, and records `{type: "feedback", status: "skipped", reason: "non-interactive"}` in `offers`, the behaviour `plugins/assess/skills/assess/references/consent-lifecycle.md` already states. Existing interactive-gate tests, not a forge corpus case.
9. With `gh` unavailable the flow prints the search URL and the anomaly code, exits without a traceback, and files nothing. Forge corpus case `adv-1`.
10. Every issue write, reaction or create, is preceded by an explicit confirmation naming the exact target and action; a decline writes nothing. Forge corpus case `adv-2`.
11. `plugins/assess/skills/assess/references/consent-lifecycle.md` describes the dedup branch of the feedback offer alongside the create branch.
12. Step 7's marker call resolves under a family install: it invokes `feedback_marker.py` through the same `SKILL_DIR` block that already runs `assess_emit_workflow.py`, and both skills sit under `plugins/assess/`, so `${CLAUDE_PLUGIN_ROOT}` reaches the module. Asserted by running Step 7's command as written from a `--plugin-dir plugins/assess` install.
13. Candidate B's PR body carries the forge report gate ledger for a `skill-forge` run over `plugins/assess/skills/assess-pr/SKILL.md` against `plugins/assess/skills/assess-pr/forge/corpus.md`. Recorded verdict.
14. After Candidate C, an explicit size argument selects the huddle mode: the size-1 gut-check transfer-set case passes when the size arrives as an argument, with no change to the no-argument case's verdict.
15. Candidate C's PR body carries the forge report gate ledger for a `skill-forge` run over `plugins/huddle/skills/huddle/SKILL.md` against the WS4 huddle transfer set. Recorded verdict.
16. Each candidate PR bumps the version of the plugin it changes in the same diff, per D2 and R10.
17. `uv run --with pytest pytest tests/test_plugin_contract.py -q` is green on each candidate PR. The path is repo-root and unchanged by PR1, which moves `skills/` and `commands/`, not `tests/`.

## Verification

Promotion means Gate 1 and Gate 2 both pass, per `gate-hierarchy.md`. Gate 1 is objective: every case in the named set passes the Fidelity judge, and one HIGH-severity Fidelity finding fails it outright, while LOW and MED findings are recorded and advisory. Gate 2 is panel confidence: all cases green and no HIGH-severity dissent, with LOW and MED dissent documented but not blocking. Gate 3 (measurable gain) and the budget ceiling decide only when the loop stops, never whether it promotes. All three targets are `SKILL.md` files, so each runs the full five-lens panel (Fidelity, Adversarial, Compression, Usability, Trigger/routing) that `judge-lenses.md` fixes for the skill artifact type.

| Candidate | Set the panel runs | Cases WS8 requires that set to contain | Recorded artefact |
|-----------|--------------------|----------------------------------------|-------------------|
| A: huddle critic line | WS4 8-case huddle transfer set (D9), enumerated in [disclosure/spec.md](../disclosure/spec.md) | A case whose task drives a huddle to a verdict and can only pass if the bounded completeness-critic pass ran, its gaps were resolved, and the verdict carried the `### Coverage` line | Forge report gate ledger in the PR body |
| B: assess-pr search-first feedback | `plugins/assess/skills/assess-pr/forge/corpus.md`, six cases authored in the PR, plus `plugins/assess/skills/assess/tests` for `feedback_marker.py` | `happy-1` no-match create, `happy-2` single-match `+1`, `edge-1` already-reacted, `edge-2` closed-only, `adv-1` `gh` unavailable, `adv-2` consent declined | Forge report gate ledger in the PR body; green pytest run |
| C: huddle size argument | The same WS4 8-case huddle transfer set | The no-argument case, whose verdict must not move, and the size-1 gut-check case, which must pass with the size supplied as an argument rather than chosen by the chair | Forge report gate ledger in the PR body |

Candidate B's corpus is six cases where `test-taxonomy.md` defaults to 3-5, because Step 7 gains six branches at once and the taxonomy's own instruction is to put a case on the boundary of each. The lens count does not scale with it: five lenses either way.

A STOP verdict is not a merge: the candidate is reworked and re-forged, the same discipline R6 applies to a failed split. `ab-equivalence` is not the gate for any candidate here, because none of them is a move.

## Breadcrumbs

WS8 adds no shim and no `docs/migration-2.0.md` row of its own.

- The `6hats` stub and its `remove-in: 3.0.0` entry belong to R12 and WS2, and the migration row that maps `/6hats` to `/huddle` belongs to R18. Candidate C leaves both alone.
- `docs/migration-2.0.md` does not exist at `44bddbf` (`ls docs/migration-2.0.md` exits 1); R18 creates it, so WS8 has no file to add rows to.
- The forge corpus Candidate B authors is a permanent test asset, not a shim: it stays after 3.0.0 and feeds the `evals/` format WS9 defines under R9.

## Rollback

Each candidate is one PR against `main`, so each reverts independently with `git revert`.

- Candidate A: a revert restores the pre-forge `plugins/huddle/skills/huddle/SKILL.md`. The completeness-critic behaviour predates WS8, so a revert removes only the amendments, never the shipped R3 behaviour.
- Candidate B: a revert removes `plugins/assess/skills/assess/scripts/lib/feedback_marker.py`, its tests, the Step 7 edits, and the forge corpus in one commit, returning Step 7 to its create-only flow. Issues already filed with an `anomaly:<code>` label survive the revert as inert labels; they carry no repo data and block nothing.
- Candidate C: a revert removes the behavioural amendments only. The `$0` guard, the `argument-hint`, and the `6hats` stub live in PR1 (R12), so `/6hats` and `/huddle` both keep working.
- No candidate touches `FLOOR.md`, a floor-marked file, a CI job `name:`, or `action.yml`, so no revert can change a required status-check context. Each revert restores green by restoring the previous commit exactly.

## Sequencing

WS8 starts after WS4 has merged, because the huddle transfer set is a WS4 prerequisite (D9) and gates candidates A and C. Candidate B depends on WS4 only for ordering, not for its corpus, and on WS3 for the final shape of the `SKILL_DIR` block its Step 7 call sits in.

| PR | Candidate | May not touch |
|----|-----------|---------------|
| 1 | A: forge the huddle completeness-critic line | `plugins/assess/`, the `6hats` stub, the huddle `argument-hint`, the transfer set itself |
| 2 | B: assess-pr search-first self-feedback | `plugins/huddle/`, `plugins/assess/skills/assess/scripts/` outside the new `lib/feedback_marker.py`, the deterministic scoring core, `run-context.json`'s schema |
| 3 | C: huddle size argument | `plugins/assess/`, the `6hats` stub, the huddle `argument-hint` and `$0` guard as landed by R12 |

PRs 1 and 3 both edit `plugins/huddle/skills/huddle/SKILL.md`, so they run in that order rather than in parallel; PR 3 forges the file PR 1 promoted. PR 2 shares no file with either and can run alongside them. WS9 reads the outcome of all three (R9), so no candidate is left unmerged when WS9 starts.
