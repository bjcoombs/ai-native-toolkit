# Expected result: known-good interactive canary

**Verdict the harness must reach: tier-1 PASS, then STALL at tier-3 (not
certified without operator sign-off).** This is the within-class positive
control (canary criterion 2b): a working interactive build in the same class as
broken jet-fighters passes its tier-1 criteria and stalls only at tier-3, while
jet-fighters fails at tier-1. A gate that actually drives builds discriminates
the two; a gate that blanket-refuses interactive products fails here.

## Freeze stage (against the interactive null artifact)

Against a stub that launches and does nothing (the interactive null artifact,
same shape as `../jet-fighters/null_artifact/`), all five criteria FAIL - no
render, no state, no input effect. The contract is not vacuous and may freeze.
The freeze gate also confirms the structural floor: this interactive contract
contains a tier-3 criterion (LR5), so B3 permits freeze.

## Cold-exit stage (against `build/`)

A cold verifier launches and drives `build/index.html`:

| id | tier | expected | observed |
|----|------|----------|----------|
| LR1 | 1 | PASS | Canvas renders; `getState()` returns `{phase:'READY', lane:1, score:0}`. |
| LR2 | 1 | PASS | Space -> `phase:'PLAYING'`. |
| LR3 | 1 | PASS | ArrowUp/ArrowDown change `lane` to an adjacent lane, clamped at 0 and 2. |
| LR4 | 1 | PASS | `f` while PLAYING increments `score`. |
| LR5 | 3 | ESCALATE | Perceptual; cold agent cannot judge. Escalated to the operator with an observed-output artifact; the run does not certify until `operator_signoff` is recorded. |

All tier-1 criteria drive and pass. The run does **not** certify `PASS` on the
machine tiers alone: it reaches tier-3 escalation and waits for the operator
(interactive class - contract green is necessary, never sufficient). `PARTIAL`
is not the right stamp either: nothing was undrivable; tier-1 is fully green and
the only remaining criterion is human-mandatory by design.

## What a verifier should observe (per class)

Interactive class, working build: the drive succeeds, tier-1 is green, and the
machinery's value is that tier-3 becomes a *required, recorded* human launch
rather than an informal one. Contrast the two interactive fixtures:

- jet-fighters `build/`: tier-1 FAILS (input never wired) -> caught before
  tier-3.
- Lane Runner `build/`: tier-1 PASSES -> stalls at tier-3 awaiting the operator.

That contrast, in one harness invocation, is the within-class discrimination the
suite must demonstrate.
