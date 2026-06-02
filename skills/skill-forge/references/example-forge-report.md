# Example forge report

This is the real output of forging `skill-forge` with `skill-forge` - the self-forge that promoted the version you are reading (Phase B of the bootstrap). It is not a mock. It is kept as the canonical example because the most honest demonstration of the harness is it finding and fixing a defect in itself: round 1 caught a HIGH-severity contradiction in skill-forge's own design (a 5-lens promise undercut by a 3-lens default), and the amendment that fixed it is in the shipped skill.

The report below follows `forge-report-template.md`.

---

# Forge report: skill-forge

**Run date:** 2026-06-02  **Mode:** phased  **Verdict:** PROMOTE

## Intent

The ground truth the Fidelity lens judged against. The intent was derived from the seed draft, every clause marked ASSUMED, and all seven accepted by the user before round 1.

| Clause | Status | Accepted by |
|--------|--------|-------------|
| Given a skill (draft or existing) plus its intent, it hardens that skill through iterative refinement rounds to a promotion decision. | assumed-accepted | user |
| It is a prove-and-promote quality gate, not an authoring tool. | assumed-accepted | user |
| Each round, a panel of five lenses judges the skill and the lead makes exactly one targeted amendment. | assumed-accepted | user |
| Promotion requires zero Fidelity failures (Gate 1) and no HIGH-severity dissent (Gate 2); it stops on diminishing returns (Gate 3) or budget, always terminating with an honest report. | assumed-accepted | user |
| It degrades across three execution modes (team / phased / solo) so it runs anywhere, including no-subagent chat. | assumed-accepted | user |
| When the skill under test is skill-forge itself, runners forge the flawed fixture (depth-1) and roles never blur. | assumed-accepted | user |
| It produces a hardened SKILL.md plus a forge report plus a persistent test corpus. | assumed-accepted | user |

Fidelity did not judge against any `assumed-rejected` clause (none were rejected).

## Test suite

Per the depth-1 recursion guard, the runner forged the **flawed-sample-skill fixture** (a commit-message-writer with one planted defect per lens), exercising skill-forge's own instructions in the process. The four cases span the taxonomy.

| Case | Type | Input summary | Origin |
|------|------|---------------|--------|
| happy-1 | happy path | Forge the fixture against a normal staged-diff commit scenario (null-check fix). | seed |
| edge-1 | edge case | Forge with an empty staged diff (boundary the fixture never guards). | seed |
| adv-1 | adversarial | Forge with a one-line README fix and no tracker system (probes the fixture's soft instructions). | seed |
| comp-1 | composition | Forge where the output must feed a `git commit -m` automation expecting `type(scope): subject`. | seed |

Persistent corpus: `skills/skill-forge/tests/fixtures/flawed-sample-skill/` (the fixture is the self-forge's standing corpus; re-forging skill-forge re-enters Phase A- to review it).

## Per-round log

| Round | Lens that prompted it | Hypothesis (expect Z to improve) | Change made | Result |
|-------|----------------------|----------------------------------|-------------|--------|
| 1 | fidelity + usability | Make the default forge 5 lenses with a deterministic selector; expect Fidelity and Usability `round_verdict` to go to better. | Reconciled the lens count across `SKILL.md` and `judge-lenses.md`: all five run by default, drop to 3/2 only on explicit quick-check, self-forge always uses 5. | improved |
| 2 | (verification) | Confirm the amendment cleared both HIGH findings without regression. | None (regression-focused re-inspection only). | Fidelity and Usability both pass; no regression. |

## Gate ledger

| Round | Gate 1 (Fidelity) | Gate 2 (no HIGH dissent) | Gate 3 (measurable gain) | Outcome |
|-------|-------------------|--------------------------|--------------------------|---------|
| 1 | fail | - | - | iterate |
| 2 | pass | pass | pass | PROMOTE |

Gate 1 failed in round 1 on a single HIGH-severity defect carried by two lenses (the lens-count contradiction). The round-1 amendment cleared it; round 2 confirmed Fidelity and Usability both pass, no new HIGH was introduced, and the remaining findings are all MED/LOW - so Gate 2 passes and the skill promotes.

## Dissent log

Severity-tagged, cumulative. Documented even though none blocked the gate (only HIGH dissent blocks Gate 2, and none survived to round 2).

| Round | Lens | Severity | Tag | Summary | Blocked a gate? |
|-------|------|----------|-----|---------|-----------------|
| 1 | fidelity | HIGH | static | Intent promised a 5-lens panel but the default forge was 3 lenses, so a faithful default run dropped Compression and Trigger. | yes - Gate 1 (round 1) |
| 1 | usability | HIGH | static | Same contradiction: a fresh runner had to stop and resolve the lens count. | yes - Gate 1 (round 1) |
| 1 | compression | MED | static | `runner-prompt.md` restated SKILL.md's wrapper rationale, re-listed its own template, and duplicated the self-report-to-lens mapping. | no |
| 1 | adversarial | MED | static | Batch-escape threshold was self-described as "tunable, not a fixed law" - gameable under in-run pressure. | no |
| 1 | adversarial | MED | static | The "Trigger never gates Gate 1" rule lived only in `judge-lenses.md`, away from Gate 1's definition - a future-edit hazard. | no |
| 1 | usability | MED | static | The lead-does-fixture-review / runners-stay-blind split was implicit. | no |
| 1 | trigger-routing + calibration | MED | static | All five planted fixture defects were caught (panel not broken), but the planted Usability defect routed to the Adversarial lens - a Usability/Adversarial lens-boundary softness. | no |
| 1 | (low) | LOW | static | Solo-mode loop termination not restated; `panel-ledger.md` Fields restated the schema; minor trigger-description overlap. | no |

## Final verdict

**PROMOTE**

- Gates met: Gate 1, Gate 2, Gate 3.
- Gates not met: none.
- Residual HIGH-severity dissent: none (the only HIGH was the lens-count contradiction, fixed in round 1).
- Best-so-far artifact: `skills/skill-forge/SKILL.md` (the promoted version).

### Post-promotion follow-ups

The MED/LOW dissent does not block promotion, but the cheap, high-value items were folded in as ordinary edits after promotion (at the user's direction): the `runner-prompt.md` compression trim, the Gate-1/static cross-reference guard in `gate-hierarchy.md`, the explicit lead-reviews-fixture / runners-stay-blind sentence, the batch-escape "fixed for the duration of a run" tightening, the solo-mode loop-termination clause, and the `panel-ledger.md` Fields trim. The one MED left as a documented design tension is the Usability/Adversarial lens-boundary softness, already acknowledged in `judge-lenses.md`.

## Rounds and waste

- Rounds run: 2 (one amendment round, one verification round).
- Budget ceiling: not reached.
- Estimated waste: ~0 - the round-1 hypothesis came back improved on first verification, so no flat round was spent.
