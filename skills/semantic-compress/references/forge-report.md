# Forge report: semantic-compress

This skill was hardened with `/skill-forge` before it shipped. The report is kept as evidence - the same prove-and-promote discipline `skill-forge` applies to itself, applied here to its first external target. The forge changed the skill's central thesis mid-run.

**Run date:** 2026-06-02  **Mode:** phased  **Verdict:** PROMOTE (3 rounds)

## Intent

Six clauses, all accepted by the user before round 1.

| Clause | Status |
|--------|--------|
| Given LLM-directed text, remove explanations of concepts the model already knows, keep what it doesn't. | assumed-accepted |
| Core knowledge -> a pointer (name/cue that activates it); project/bespoke -> explicit verbatim. | assumed-accepted (revised - see below) |
| Keep novel/specific info verbatim: facts, constraints, decisions, non-standard twists, disambiguating detail. | assumed-accepted |
| Output must be self-sufficient: the model can act on it without the dropped parts. | assumed-accepted |
| Output is clean stripped text - no markers, no meta-commentary. | assumed-accepted |
| Applies only when the LLM is the audience; human-directed text is left alone. | assumed-accepted |

## Test suite

| Case | Type | Input summary |
|------|------|---------------|
| T1 | happy | A definition of idempotency plus endpoint-specific behaviour. |
| T2 | edge (all-novel) | Only specific infra facts. |
| T3 | edge (all-known) | Pure OOP definitions, no bespoke, no instruction. |
| T4 | adversarial | A non-standard local redefinition of "optimistic locking". |
| T5 | adversarial | A definition aimed at human new hires, not the model. |
| T6 | composition | A nested "preprocess this instruction" wrapper around a SOLID refactor brief. |

## Per-round log

| Round | Prompted by | Hypothesis | Change | Result |
|-------|-------------|-----------|--------|--------|
| 1 | runner (T3 empty output, T5 trigger-vs-body conflict) | The original elision-only thesis ("drop what the model knows") is the wrong model; a pointer reliably activates core knowledge where deletion gambles. | Thesis change (user decision): point-for-core / explicit-for-bespoke. Rewrote the skill. | improved |
| 2 | validation runner | The new model should handle all six cases correctly. | None (re-observe). | All 6 correct: pointer activates (T1), twist kept (T4), degenerate -> pointers (T3), audience gate in body (T5). |
| 3 | panel (Usability HIGH) | The skill is silent on nested/quoted instructions, a foreseeable case for an instruction-processing skill. | Added Step 0 nested/wrapped-instruction handling. | Gate cleared. |

## Gate ledger

| Round | Gate 1 (Fidelity) | Gate 2 (no HIGH dissent) | Outcome |
|-------|-------------------|--------------------------|---------|
| 2 | pass | fail (Usability HIGH: nested-instruction silence) | iterate |
| 3 | pass | pass | PROMOTE |

## Dissent log

| Lens | Severity | Tag | Summary | Blocked? |
|------|----------|-----|---------|----------|
| usability | HIGH | behavioural | Nested/quoted-instruction case unspecified (T6). | yes - Gate 2 round 2 |
| adversarial | MED | static | "Frame implied" was a self-certifying escape that could collapse the skill back to deleting core knowledge. | no |
| usability | MED | behavioural | Audience gate gave no tie-breaker for self-directed cues ("Recall that..."). | no |
| fidelity | LOW | behavioural | A pointer can be mildly redundant when a bespoke span already names the concept. | no |

## Final verdict

**PROMOTE.** Gate 1 and Gate 2 met. The HIGH (nested-instruction) was fixed in round 3. The point-vs-delete MED was folded in post-promotion (pointer always required unless a surviving bespoke span literally names the concept), which also resolved the audience self-cue MED and the LOW redundancy. No residual HIGH dissent.

## Rounds and waste

- Rounds run: 3 (one thesis-change amendment, one validation, one HIGH fix).
- Estimated waste: ~0 - every round changed the verdict.
