# WS4 spec: Disclosure and clarity

## Intent

Brief R6 of the programme intent ([intent.md](../intent.md), index [README.md](../README.md)), carrying decision D9; parsed into Task Master tag `modernization-disclosure`.

## Current state

Measured at 44bddbf on this checkout. Every number below sits next to the command that produced it.

Body line counts for every skill and agent, frontmatter excluded. The `awk` program drops the leading `---` block and counts what remains:

```
$ git rev-parse --short HEAD
44bddbf

$ for f in skills/*/SKILL.md agents/*.md; do
    n=$(awk 'NR==1 && $0=="---" {fm=1; next} fm && $0=="---" {fm=0; next} !fm {c++} END {print c+0}' "$f")
    printf '%4d  %s\n' "$n" "$f"
  done | sort -rn
 550  skills/marathon/SKILL.md
 532  agents/assess-layer-scorer.md
 515  skills/huddle/SKILL.md
 494  skills/assess/SKILL.md
 390  skills/assess-findings/SKILL.md
 339  skills/assess-pr/SKILL.md
 225  skills/semantic-compress/SKILL.md
 210  skills/pr-review-merge/SKILL.md
 171  skills/skill-forge/SKILL.md
 125  skills/deslop/SKILL.md
 124  skills/ab-equivalence/SKILL.md
 117  skills/ghsync/SKILL.md
  90  agents/black-hat.md
  87  skills/ghreport/SKILL.md
  80  agents/scribe.md
  77  agents/red-hat.md
  63  agents/white-hat.md
  63  agents/green-hat.md
  51  agents/yellow-hat.md
  51  agents/blue-hat.md
  23  agents/README.md
```

Three bodies exceed 500. `assess` at 494 is compliant and is not split (programme Out of scope). `agents/README.md` is deleted by WS1 (R3) and is not a WS4 concern.

Where the lines sit in the three oversize bodies. The `awk` program prints the span of each level-2 heading; the same program with `/^### /` prints level-3 spans:

```
$ awk '/^## /{if(prev!=""){printf "%4d  %s\n", NR-prevline, prev} prev=$0; prevline=NR} END{printf "%4d  %s\n", NR-prevline+1, prev}' skills/huddle/SKILL.md
   9  ## Architecture
  18  ## Hat Findings Schema
  31  ## Capability Requirements
   5  ## No Arguments Behavior
 148  ## Protocol
   4  ## Your Identity
   4  ## Your Teammates
  12  ## IMMEDIATE FIRST TASK
   9  ## How Subsequent Phases Work
  11  ## Communication Style
  10  ## Hat Agent Prompts
 100  ## Hat Agent Output Format
  61  ## Chairperson's Summary
  33  ## Discovery Mode: Loop-Until-Dry
  35  ## Facilitation Principles
  13  ## Lessons Learned
   3  ## Usage
   4  ## Examples

$ awk '/^## /{if(prev!=""){printf "%4d  %s\n", NR-prevline, prev} prev=$0; prevline=NR} END{printf "%4d  %s\n", NR-prevline+1, prev}' skills/marathon/SKILL.md
  14  ## Work-Source Adapter Contract
  34  ## Phase 0: Capability Detection
  13  ## Execution Modes
  10  ## Entry Gate (non-removable)
  53  ## Step 1: DAG + Hot-File Analysis
  69  ## Step 2: Team + Tracking
  33  ## Step 3: Spawn Teammates
   3  ## Setup
   3  ## Requirements
   4  ## Architectural Direction
   3  ## Project Guidelines
   3  ## Shell Rules
   5  ## Known Conflict Patterns
   6  ## Workflow
  10  ## Communication
  20  ## Teammate Event Payloads
   4  ## Scope
  11  ## Lifecycle
   4  ## Step 4: Lead Monitoring
  62  ## Marathon Started: <tag>
  54  ## Smart Merge
  47  ## Crash Recovery
  28  ## Completion + Retrospective
  45  ## Marathon Complete: <tag>
   3  ## Subagent Fallback (no teams)

$ awk '/^### /{if(prev!=""){printf "%4d  %s\n", NR-prevline, prev} prev=$0; prevline=NR} END{printf "%4d  %s\n", NR-prevline+1, prev}' agents/assess-layer-scorer.md
  99  ### Layer 0: Agent Instructions & Navigability (Read-Side Foundation)
  42  ### Layer 1: Runtime Legibility / Liveness (Read-Side Foundation)
  35  ### Layer 2: Code Design (Compile-Time Correctness)
 113  ### Layer 3: Linters (Style and Correctness Enforcement)
  24  ### Layer 4: Architecture Tests (Conventions as Contracts)
  33  ### Pre-check: Test Inventory
  32  ### Layer 5: CI Pipeline (Automated Safety Net)
  34  ### Layer 6: Coverage Gates (Test Completeness Enforcement)
  18  ### Layer 7: Automated Code Review (Design-Level Feedback)
  46  ### Layer 8: AI Project Management (Orchestration and Feedback) - Capstone
```

The level-2 headings inside `## Marathon Started: <tag>`, `## Marathon Complete: <tag>` and the `## Your Identity` block of huddle are prompt templates quoted for a teammate, not sections of the host document.

Floor tokens carried by `skills/marathon/SKILL.md`. The file is one of the four in `scripts/floor_check.py`'s `MARKED_FILES`, so any WS4 edit to it is compared token-by-token against merge-base:

```
$ for t in 'floor:cold-verify-completion' 'start_gate.py' 'spawn_verifier.py' 'complete_gate.py'; do
    printf '%2d  %s\n' "$(grep -c -F "$t" skills/marathon/SKILL.md)" "$t"; done
 2  floor:cold-verify-completion
 2  start_gate.py
 1  spawn_verifier.py
 1  complete_gate.py

$ grep -n -E 'floor:cold-verify-completion|start_gate\.py|spawn_verifier\.py|complete_gate\.py' skills/marathon/SKILL.md | cut -d: -f1 | tr '\n' ' '
15 89 92 492 506
```

Five line numbers, four of which sit in a section. Line 15 is a standalone `<!-- floor:cold-verify-completion -->` anchor in the preamble, ahead of the first heading (`## Work-Source Adapter Contract` at 23), so it belongs to no section and no candidate span touches it. `standalone_anchor_count` (`scripts/floor_check.py:78`) counts only lines whose stripped content is the bare marker, which makes line 15 the anchor the check treats as load-bearing and line 506 - a backtick-wrapped mention inside prose - not an anchor at all. Lines 89 and 92 sit in `## Entry Gate (non-removable)` (10 lines); 492 and 506 sit in `## Completion + Retrospective` (28 lines). Both sections are token-bearing and stay in the body.

Marathon forge cases. The corpus holds five seed cases and no wave, shutdown, gate or retrospective case:

```
$ rg -n '^### ' skills/marathon/forge/corpus.md | cut -d' ' -f1,2
8:### happy-1
32:### edge-1
45:### adv-1
59:### comp-1
73:### crash-1
```

`edge-1` is the missing-Marathon-Configuration branch. The branch it exercises prints a pointer that WS1 (R2) retires. Five live occurrences remain outside test fixtures, one of them under `skills/`. The first command prints file and line only, and the second counts links rather than printing them: two of the five sites carry the pointer as a markdown link, and a link target quoted verbatim inside this file would be read as a link *from this file* by any relative-link check run over `docs/design/`, where it does not resolve.

```
$ rg -n -o 'tm-marathon-config-example' skills/ agents/ commands/ docs/index.md | grep -v fixtures | cut -d: -f1,2 | sort -u
commands/README.md:20
commands/tm-marathon-config-example.md:2
commands/tm.md:62
docs/index.md:54
skills/marathon/SKILL.md:64

$ rg -c '\]\([^)]*tm-marathon-config-example[^)]*\)' commands/README.md commands/tm.md docs/index.md skills/marathon/SKILL.md | sort
commands/README.md:1
docs/index.md:1
```

Five sites, of which two - `commands/README.md:20` and `docs/index.md:54` - hold the pointer as a markdown link, which is what the second command counts. `skills/marathon/SKILL.md:64` is the only one WS4 touches. The three under `commands/` die with the directory WS1 deletes under R2, and the `docs/index.md` link dies with them; none is WS4 work, which is why Requirement 3 is scoped to `skills/` and `agents/`.

Existing `references/` files and their line counts. Five skills carry one; `huddle`, `marathon` and the scorer carry none:

```
$ find skills agents -path '*/references/*.md' | sort | while read f; do printf '%4d  %s\n' "$(wc -l < "$f")" "$f"; done
 103  skills/ab-equivalence/references/ab-equivalence.md
 113  skills/ab-equivalence/references/equivalence-judge-prompt.md
 146  skills/ab-equivalence/references/runner-prompt.md
  82  skills/assess/references/actions-schema.md
  73  skills/assess/references/consent-lifecycle.md
  85  skills/assess/references/monorepo-scoping.md
  82  skills/assess/references/uninstall.md
 126  skills/deslop/references/full-checklist.md
  70  skills/semantic-compress/references/battle-scar-classifier.md
  40  skills/semantic-compress/references/cognitive-ergonomics.md
  96  skills/semantic-compress/references/directive-clarity-patterns.md
  73  skills/semantic-compress/references/directive-clarity-rewrites.md
 223  skills/semantic-compress/references/distill-loop.md
 116  skills/semantic-compress/references/distillation-report-template.md
 105  skills/semantic-compress/references/transfer-set-design.md
 110  skills/skill-forge/references/forge-report-template.md
  57  skills/skill-forge/references/gate-hierarchy.md
  81  skills/skill-forge/references/judge-lenses.md
  88  skills/skill-forge/references/panel-ledger.md
  48  skills/skill-forge/references/test-taxonomy.md
```

No `evals/` directory exists anywhere in the tree except the one this programme's own docs create:

```
$ find . -type d -name evals -not -path './.git/*'
./docs/design/2026-09-modernization/evals
```

Two mechanical facts the design depends on, read from the transformer:

- `scripts/transform_skill.py:202-213` copies every file under a skill's `source_dir` and runs the same `chat-skip` / `chat-replace` marker transform over each `.md`. A span moved from a `SKILL.md` into that skill's own `references/` keeps its markers working.
- `scripts/standalone_skill_config.py:137` bundles `agents/assess-layer-scorer.md` into the assess ZIP as `references/assess-layer-scorer.md`. It needs that entry because it sits outside the assess `source_dir` (`skills/assess`, line 55); a file that sits inside the `source_dir` needs no entry, because the rglob above already copies it.

## Design

Progressive disclosure, gated on behaviour, one plugin per PR. Every decision below is inherited: D9 fixes the transfer set as a prerequisite, R6 fixes the 500-line ceiling and the one-level-deep reference shape, R10 fixes the same-PR version bump, R16 keeps behaviour change out of this workstream.

### Huddle transfer set (D9)

Eight cases under `plugins/huddle/evals/<type>-<slug>/` with `prompt.md` and `graders/criteria.md`, the CLI's own second format (R9). Types come from `skills/skill-forge/references/test-taxonomy.md`:

| Case directory | Grading | What it holds the split to |
|----------------|---------|----------------------------|
| `edge-no-argument` | script | The exact question emitted with no topic, from `## No Arguments Behavior` |
| `happy-size-1-gut-check` | script | Size 1 selects solo flat-parallel and announces it |
| `happy-size-5-team` | script | Size 5 with the flag selects team mode, forms the team, announces it |
| `edge-deferred-tools-probe` | script | Both arms: with `SendMessage` live and with it deferred behind `ToolSearch`, the chair probes before deciding and does not fall to phased on a false negative |
| `composition-wind-down` | script | Step 6 shutdown handshake runs to completion, every teammate stood down |
| `adversarial-reframe` | LLM-judged | A topic stated as a solution; the chair reframes at Step 1 rather than analysing the stated answer |
| `edge-discovery-classification` | LLM-judged | Loop-until-dry classification of a finding that spans two hats |
| `composition-critic-pass` | LLM-judged | Step 4b completeness critic finds the gap the hats left |

Five script-graded, three LLM-judged. That split is the eval harness's grading mode - runnable grader script against LLM rubric - and is not the `ab-equivalence` equivalence judge, which is a different component invoked once per case unconditionally (`skills/ab-equivalence/SKILL.md:33-37`; the judge prompt "is filled once per transfer-set case", `references/equivalence-judge-prompt.md:5`). The five script-graded cases are WS9's huddle evals unchanged (D9). All eight run through `ab-equivalence`'s narrating solo runner (`skills/ab-equivalence/references/runner-prompt.md`): round 1 is 8 teacher plus 8 candidate runner invocations plus 8 judge invocations, 24 in all. Teacher transcripts are captured once and reused (`skills/ab-equivalence/SKILL.md:43`), so each later round is 8 candidate plus 8 judge. The directive-clarity stage below adds a fresh teacher capture over the split checkpoint (`skills/semantic-compress/SKILL.md:224`), another 8 runner invocations per split PR.

Rejected: authoring the set inside the split PR. The transfer set is the gate; a gate written against the document it grades measures nothing.

The scorer's four cases are authored in the same PR-A, in the same directory shape, under `plugins/assess/evals/`. They need no new fixture: each case's input is a golden fixture that already exists (`skills/assess/tests/fixtures/golden/run-context-baseline.json`, and run-contexts over `hollow_test_repo`, `honest_test_repo` and `lean_with_skills`), and the case directory is only the `prompt.md` plus `graders/criteria.md` wrapper the CLI's second format needs. All four are script-graded against the layer verdicts the fixture pins.

### Marathon forge-case extension

`skills/marathon/forge/corpus.md` gains five cases in the existing naming scheme, covering the five branches the seed corpus never exercises:

| Case | Type | Branch |
|------|------|--------|
| `happy-2` | happy path | Wave transition: Wave 1 merged, Wave 2 spawn decision |
| `adv-2` | adversarial | Early shutdown: a teammate stands down before `REVIEW_CLEAR` |
| `edge-2` | edge case | Entry gate fails closed with no freeze evidence |
| `edge-3` | edge case | Completion gate rejects a record with no verifier results |
| `comp-2` | composition | Retrospective and the PRD delivery check at completion |

`edge-2`, `edge-3` and `comp-2` cover the two token-bearing sections, so the corpus itself detects a split that hollows the floor obligation. The `/tm-marathon-config-example` pointer at `skills/marathon/SKILL.md:64` is repointed at `references/config-example.md` (the file WS1 creates under R1) before any case runs, so `edge-1` stops asserting a branch that names a retired command.

### Where the lines go

One level deep, linked directly from the body, contents list on any file over 100 lines (R6).

| Body | Moves to | Lines out | Body after |
|------|----------|-----------|------------|
| `huddle` | `references/hat-output-format.md` (from `## Hat Agent Output Format`) | 100 | 417 |
| `huddle` | `references/chairperson-summary.md` (from `## Chairperson's Summary`) | 61 | 358 |
| `marathon` | `references/teammate-brief.md` (from `## Marathon Started: <tag>`) | 62 | 490 |
| `marathon` | `references/retro-template.md` (from `## Marathon Complete: <tag>`) | 45 | 447 |
| `assess-layer-scorer` | `references/layer-3-linters.md` (from `### Layer 3`) | 113 | 421 |

Each moved span leaves a one-line pointer, so the arithmetic above is the floor of the saving, not the ceiling. The spans are candidates, not a contract: the contract is the ceiling in R6 and the per-case verdict in Verification. A span whose move regresses a case is put back and a different span chosen.

Two placement constraints decide the scorer's reference location:

- R3 fails any `.md` under an `agents/` path that is not a valid agent, so `plugins/assess/agents/references/` is not available. The scorer's reference lands in the assess skill at `plugins/assess/skills/assess/references/layer-3-linters.md` and the agent links it as `../skills/assess/references/layer-3-linters.md`.
- That path is correct in the repo and wrong in the standalone ZIP, where the scorer body ships as `references/assess-layer-scorer.md` and the reference ships as a sibling. The link is wrapped in a `chat-replace:scorer-reference-path` marker whose standalone replacement is the sibling path. No `bundle_files` entry is added: the reference lands inside the assess skill's `source_dir`, so `transform_skill.py:202-213` already copies it into the ZIP as `references/layer-3-linters.md` and runs the marker transform over it. Rejected: leaving the link untransformed, which ships a dangling path and fails `test_no_dangling_cross_skill_references[assess]`.

Marathon's floor tokens stay in the body. `## Entry Gate (non-removable)` and `## Completion + Retrospective` are not split, moved, or summarised; a reference file cannot carry the obligation because `floor_check.py markers` reads `skills/marathon/SKILL.md` itself - the file is one of `MARKED_FILES` - and `removed_tokens` (`scripts/floor_check.py:91`) fails on either signal: a drop in a token's occurrence count, or the loss of the standalone anchor line. Tokens that leave the body for a reference file read as removed on both.

Huddle's `## Protocol` (148 lines) stays. It is the step sequence the skill is, and the spawn machinery inside it already leaves the standalone ZIP through the `chat-skip` region opening at `skills/huddle/SKILL.md:192`. Moving it into a reference buys ZIP size that is already saved and costs the chair a hop in the middle of the protocol.

### Directive-clarity pass

In the same PR as each split, `semantic-compress`'s directive-clarity transform runs over the split result as the second stage of the staged-with-checkpoint order it defines (`skills/semantic-compress/SKILL.md:213-229`): the split body is the checkpoint and the revert floor, so a regressing rewrite reverts to the split without losing it. The transform's own gate is stricter than compression's - no regression **and** a measured directness gain on the rewritten cases - and a rewrite that loses nothing but measures no gain is dropped.

### Contract test

`tests/test_plugin_contract.py` gains a body-line ceiling parametrized over every SKILL.md and agent file, counting with the frontmatter-skipping rule used in Current state above and asserting under 500. It lands in the last split PR, because it is red until every body is under the ceiling.

## Requirements

1. `plugins/huddle/evals/` holds the eight case directories in the Design table, each with `prompt.md` and `graders/criteria.md`; five carry a runnable grader script, three carry an LLM rubric. `plugins/assess/evals/` holds the four scorer case directories in the Verification table in the same shape, each naming an existing golden fixture as its input and carrying a runnable grader script; no new fixture is authored. Both sets land in PR-A, ahead of every split. Verified by directory listing and by `ab-equivalence` consuming each set.
2. `skills/marathon/forge/corpus.md` holds ten cases: the five in Current state plus `happy-2`, `adv-2`, `edge-2`, `edge-3`, `comp-2`. Verified by `rg -c '^### ' <corpus>` printing 10.
3. No live reference to `/tm-marathon-config-example` remains under `skills/` or `agents/` outside `skills/assess/tests/fixtures/`. The `commands/` and `docs/index.md` occurrences in Current state belong to WS1 (R2) and are out of scope here. Verified by `rg -n 'tm-marathon-config-example' skills/ agents/ | grep -v fixtures` returning no match.
4. Every `SKILL.md` body and every agent body is under 500 lines, frontmatter excluded. Verified by the new contract test and by re-running the Current state counting command.
5. Every reference file introduced sits exactly one directory below its owning skill, and any file over 100 lines opens with a contents list. Verified by path depth check and by reading the first block of each file over 100 lines.
6. Every relative link from a split body to its reference files resolves, and `test_internal_links_resolve` covers agent bodies. Today it is parametrized over `shipped_md()` (`tests/test_plugin_contract.py:79-80`), which is skill and command files only, so the scorer's `../skills/assess/references/layer-3-linters.md` link would go unchecked; PR-D widens the parametrization to include `agents/*.md` alongside the ceiling test, which already walks `agents/`. Verified by `tests/test_plugin_contract.py::test_internal_links_resolve` reporting a case id for `agents/assess-layer-scorer.md`.
7. Each split records an `ab-equivalence` per-case verdict in its PR body, with zero `candidate-regressed`. Verified by the recorded verdict.
8. Each directive-clarity pass records `original_directness` and `candidate_directness` per rewritten case; rewrites without a gain are dropped. Verified by the recorded efficiency signal.
9. `skills/marathon/SKILL.md` carries the same four floor tokens at the same or higher counts than merge-base. Verified by the `floor enforcement` required check and by the Current state `grep -c` command.
10. The standalone ZIPs still build and carry no dangling reference. Verified by `scripts/tests/test_integration.py::test_no_dangling_cross_skill_references` for `huddle` and `assess`.
11. Every PR bumps the version of each plugin it touches, and the meta-plugin, per R10. Verified by the same-PR bump contract test.
12. No PR in this workstream changes an intended behaviour. Behaviour candidates belong to WS8 under a `skill-forge` promotion verdict (R16). Verified by the `ab-equivalence` verdict showing no `candidate-diverged` outside the directive-clarity rewrites.

## Verification

The gate is `ab-equivalence` over a named set, run per split, with the per-case verdict pasted into the PR.

| Split | Transfer set | Cases |
|-------|--------------|-------|
| `huddle` | `plugins/huddle/evals/` | The eight cases in the Design table |
| `marathon` | `skills/marathon/forge/corpus.md` | Ten: `happy-1`, `happy-2`, `edge-1`, `edge-2`, `edge-3`, `adv-1`, `adv-2`, `comp-1`, `comp-2`, `crash-1` |
| `assess-layer-scorer` | `plugins/assess/evals/`, authored in PR-A over the existing golden fixtures | Four: `skills/assess/tests/fixtures/golden/run-context-baseline.json`, and run-contexts over `hollow_test_repo`, `honest_test_repo`, `lean_with_skills` |

Each run compares the pre-split body (teacher) against the split body (candidate) on identical input and returns one of `equivalent`, `candidate-diverged`, `candidate-regressed` per case. A single `candidate-regressed` blocks the merge: the span comes back into the body and a different span moves. The recorded artefact per PR is the case table with a verdict per row, the regression note where one exists, and the efficiency signal.

`skill-forge` is not the gate here. These are moves, so `ab-equivalence` freezes them; `skill-forge` gates intended change and belongs to WS8.

The deterministic checks that run alongside: `tests/test_plugin_contract.py` (link resolution, frontmatter, the new ceiling), `scripts/tests/test_integration.py` (ZIP build and dangling references), and `floor enforcement` on the marathon PR.

## Breadcrumbs

None. This workstream leaves no shim, no alias, and no `remove-in` marker: a reference file is a live path, not a compatibility layer. The one migration row in this area, `/tm-marathon-config-example` to `marathon/references/config-example.md`, is owned by WS1 under R2 and is not duplicated here.

## Rollback

Each PR is one plugin's body, its reference files, and that plugin's version bump. Reverting one restores that body whole and deletes its reference files in the same commit, so no link is left dangling and `main` returns to the state the previous merge left green.

Two ordering facts make the revert clean:

- The 500-line ceiling test lands in the last split PR. Reverting an earlier split with the test already on `main` would leave `main` red, so the ceiling test is reverted with it or the last PR is reverted first.
- The marathon revert restores the four floor tokens at their merge-base counts, which is what `floor_check.py markers` asserts, so no floor edit and no sign-off is involved in either direction.

The transfer-set PR is not reverted to fix a split. A failing split is reworked; the set is the measurement, not the change.

## Sequencing

WS4 starts after WS1 has merged, because every path below is a post-move path.

1. **PR-A, transfer sets and corpus.** The eight huddle eval cases, the four assess scorer eval cases, the five marathon forge cases, and the `/tm-marathon-config-example` repoint. May not touch any body under `skills/` except that one pointer line. Bumps `huddle`, `assess` and `delivery`.
2. **PR-B, `huddle`.** The split, the reference files, the directive-clarity pass, the eight-case verdict. May not touch `marathon`, the scorer, or the contract test.
3. **PR-C, `marathon`.** The split, the reference files, the directive-clarity pass, the ten-case verdict. May not touch `huddle`, the scorer, the contract test, or the two token-bearing sections.
4. **PR-D, `assess-layer-scorer`.** The split, the reference file, the `chat-replace` marker on the link, the four-case verdict, the widening of `test_internal_links_resolve` to agent bodies, and the 500-line ceiling contract test. May not touch `huddle` or `marathon`.

PR-B, PR-C and PR-D touch disjoint plugins and can run in parallel once PR-A has merged, except that PR-D carries the ceiling test and therefore merges last. WS8 and WS9 start after PR-D.
