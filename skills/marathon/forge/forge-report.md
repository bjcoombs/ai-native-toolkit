# Forge report: marathon

**Run date:** 2026-06-03  **Mode:** phased sub-agent (panel ledger as cross-round memory)  **Verdict:** PROMOTE

Standard run (target ≠ skill-forge), all 5 lenses, budget ceiling 5 rounds (used all 5).

## Intent

The ground truth Fidelity judged against. No intent was supplied, so all clauses were derived
from the draft and marked ASSUMED. The user declined the per-clause accept/reject prompt; per the
prompt's stated default (unselected = accepted) all clauses were treated assumed-accepted. Fidelity
judged against every clause.

| Clause | Status | Accepted by |
|--------|--------|-------------|
| A: Trigger via /tm, /issues, or explicit "run queue to completion with Agent Teams"; not a direct end-user single-task skill | assumed-accepted | default (user did not reject) |
| B: Source-agnostic — caller-supplied adapter (enumerate/mark/close/branch); no hard-coded work source | assumed-accepted | default |
| C: Derives DAG, finds hot files, combines units sharing hot files into one teammate as PRIMARY conflict-avoidance; dependencies are fallback | assumed-accepted | default |
| D: One fresh ephemeral teammate per unit or combined group; never reused | assumed-accepted | default |
| E: Lead delegates >~30s; teammates stand down at REVIEW_CLEAR and don't run CI-watch loops; lead owns CI watch, claude-review wait, merge | assumed-accepted | default |
| F: Every PR via pr-review-merge; merge in hot-file order; cleanup only after verified state==MERGED | assumed-accepted | default |
| G: Worktrees + pr-tracking.json survive crashes; reconcile vs source-of-truth + GitHub on restart | assumed-accepted | default |
| H: At completion, PRD delivery check vs merged PRs + structured retrospective | assumed-accepted | default |

No clause was assumed-rejected; Fidelity judged against all eight.

## Test suite

| Case | Type | Input summary | Origin |
|------|------|---------------|--------|
| happy-1 | happy path | /tm queue, mixed deps, one barrel hot file (1,2); tests B,C,D,E | seed |
| edge-1 | edge | 6 purely-additive units all on src/db/schema.ts + no Marathon Config; /issues adapter; tests C-fallback, missing-config, B | seed |
| adv-1 | adversarial | mid-run: idle+DIRTY conflict, AI-docs claude-review pending, "90s test fix"; tests E,F + rationalization | seed |
| comp-1 | composition | UNSTABLE + stale CR + solo repo; tests E,F + defer-to-pr-review-merge | seed |
| crash-1 | edge/recovery | restart, reconcile 4 tasks; tests G | seed |

No cases were added mid-run (no new failure mode surfaced that an existing case didn't already exercise).
Persistent corpus: `skills/marathon/forge/corpus.md` (in the target skill's forge directory).

## Per-round log

| Round | Lens that prompted it | Hypothesis (expect Z to improve) | Change made | Result |
|-------|----------------------|----------------------------------|-------------|--------|
| 1 | (baseline) | — | none (OBSERVE + INSPECT only) | — |
| 2 | Adversarial (HIGH) | Stating teammate-shutdown (early, REVIEW_CLEAR) and cleanup (later, MERGED-gated) as two stages clears the shutdown-ordering contradiction → adversarial better | Smart Merge two-stage paragraph + after-merge step-2 rewrite | improved |
| 3 | Adversarial (HIGH) | Bounding combining (ceiling, don't-collapse-wave, dependent/cx8+ sequence, version-counter sequential, 5+→consolidation) closes the no-upper-bound purpose-defeat → adversarial better | Step 1.6/1.7 combine ceiling | improved |
| 4 | Usability/Adversarial/Trigger (MED cluster, escape unlocked) | Batch 8 independent fixes (trigger label, missing-config wording, combined-group identity, retro-skip, idle+DIRTY sole-exception, additive-vs-serialize, duplicate-4, metric trim) clears the MED cluster → usability better | 8-cluster batch | improved, but introduced 1 MED clause-C regression (additive→parallel rule leaked to happy-1's 2-task case) |
| 5 | Fidelity (MED regression) | Scoping the additive→parallel split to 5+-on-one-file and stating 2-4 coupled additive pairs still combine restores clause C on happy-1 without disturbing edge-1 → fidelity better | Step 1.7 scope fence | improved |

## Gate ledger

| Round | Gate 1 (Fidelity) | Gate 2 (no HIGH dissent) | Gate 3 (measurable gain) | Outcome |
|-------|-------------------|--------------------------|--------------------------|---------|
| 1 | pass | fail (2 HIGH) | — (baseline) | iterate |
| 2 | pass | fail (1 HIGH) | pass | iterate |
| 3 | pass | pass | pass | (chose to harden MED cluster, not promote) |
| 4 | pass (happy-1 MED, advisory) | pass | pass (with 1 MED regression) | iterate (fix self-introduced regression) |
| 5 | pass | pass | pass | **PROMOTE** |

Note on round 3: Gate 1 ∧ Gate 2 first passed at round 3 — the skill was already promotable. The lead
chose to spend the remaining budget hardening the documented MED cluster (combined-group identity was a
real gap in the primary mechanic and broke the teammate Scope guard) rather than ship with known gaps.

## Dissent log

| Round | Lens | Severity | Tag | Summary | Blocked a gate? | Resolved |
|-------|------|----------|-----|---------|-----------------|----------|
| 1 | adversarial | HIGH | behavioural | shutdown-ordering self-contradiction (3-lens consensus) | yes — Gate 2 | round 2 |
| 1 | adversarial | HIGH | behavioural | "always prefer combining" no upper bound → destroys parallelism | yes — Gate 2 | round 3 |
| 1 | adversarial/usability | MED | behavioural | combined-group identity undefined; breaks Scope guard | no | round 4 |
| 1 | adversarial | MED | behavioural | lead-never-executes vs idle+DIRTY carve-out, no cross-ref | no | round 4 |
| 1 | adversarial | MED | behavioural | version-counter hot file wants sequential merge not combine | no | round 3 |
| 1 | usability | MED | behavioural | mandatory pre-spawn retro read with no input when $RETRO_LOG unset | no | round 4 |
| 1 | usability | MED | behavioural | combine-vs-fallback boundary fuzzy + no PR-size cap | no | round 3 |
| 1 | trigger-routing | MED | static | no "library skill" self-label → direct over-fire risk | no (static; Gate 2 only) | round 4 |
| 1 | trigger-routing | LOW | static | narrow trigger vocab (tag/issue/queue) → under-fire | no | round 4 |
| 1 | usability | LOW | behavioural | missing-config "prompt before starting" vs scripted proceed | no | round 4 |
| 3 | usability/adversarial | MED | behavioural | consolidation task over-serializes a purely-additive 5+ file | no | round 4 |
| 4 | fidelity | MED | behavioural | additive→parallel rule (scoped 5+) leaked to happy-1's 2-task case, softened clause C (self-introduced) | no (advisory) | round 5 |
| 5 | adversarial | LOW | behavioural | line 96 unqualified "5+ tasks on one file" vs line 99 additive refinement | no | documented (non-blocking) |
| 4 | compression | LOW | behavioural | lead-side two-stage-shutdown double-statement (L342-347 vs L361) | no | documented (non-blocking) |
| 1 | fidelity/adversarial | LOW | behavioural | crash-1 task-4 (pending + no tracking entry) matches no reconciliation rule | no | documented (non-blocking) |

## Final verdict

**PROMOTE**

- Gates met: Gate 1 (Fidelity — all cases pass, no HIGH), Gate 2 (no open HIGH dissent), Gate 3 (every amend round registered gain on its targeted lens).
- Gates not met: none.
- Residual HIGH-severity dissent: none.
- Documented non-blocking residuals (LOW, for a future re-forge): (1) crash-1 task-4 pending+no-entry non-total reconciliation rule; (2) lead-side two-stage-shutdown double-statement L342-347 vs L361 (safe ~2-line trim); (3) Step 1.7 line 96 unqualified "5+ tasks on one file" phrasing vs line 99's additive refinement.
- Best-so-far / promoted artifact: `skills/marathon/SKILL.md` (this worktree).

## Rounds and waste

- Rounds run: 5 (of 5 budget)
- Estimated waste: ~1 round. Round 4's 8-cluster batch introduced one MED clause-C regression (the additive→parallel rule leaked below its 5+ scope), which round 5 spent fixing. A tighter round-4 batch — scoping the additive-vs-serialize rule to 5+ *at the moment it was written* — would have avoided the round-5 cycle. The other 7 round-4 fixes all landed cleanly. Net: the batch traded one extra verification round for closing the whole MED cluster in one pass, which still beat 7 separate one-change rounds.

## Retrospective (which lens caught what)

- **Adversarial** carried the run: both promotion-blocking HIGHs (shutdown-ordering, combining-no-upper-bound) and the sharpest MEDs (idle+DIRTY over-application, version-counter-wants-sequential, the round-5 boundary check). The "read fields 4/5 — improvisation + wanted-to-deviate" instruction paid off: every HIGH traced to a runner's field-5 "I wanted to deviate but the skill is contradictory."
- **Usability** independently corroborated 4 of the Adversarial findings (the value of >1 lens on the same transcript) and owned the combined-group-identity + mandatory-retro gaps.
- **Trigger/routing** (static) caught the one defect no behavioural lens could see: the description's missing "library skill" self-label and the direct over-fire path.
- **Fidelity** was the safety net on the self-introduced round-4 regression — it alone ruled that the batch's additive edit had pulled happy-1 away from clause C, and correctly graded it MED (advisory) not HIGH so it didn't false-block Gate 1.
- **Compression** was lowest-yield (all LOW), but its "decorative metrics vs load-bearing calibration" distinction kept the round-2/3/4 additions honest and flagged the one real bloat (lead-side restatement).
- **Strongest signal heuristic confirmed:** a contradiction two independent runners tripped over (shutdown-ordering: comp-1 + adv-1) was the highest-confidence finding of the run — evidence, not speculation.
