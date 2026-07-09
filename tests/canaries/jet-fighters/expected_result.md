# Expected result: jet-fighters canary

**Verdict the harness must reach: NEVER CERTIFIES.** This is the regression
fixture the whole PRD exists for - the real jet-fighters run that went 100% done
/ 100% merged and shipped an unplayable game. Canary criterion 1: the actual PRD
+ broken build never certifies, and the discriminating failure is *detected*
(tier-1 launch/drive criteria FAIL at cold exit), not merely filed `PARTIAL`
because the class is interactive.

## Freeze stage (against `null_artifact/`)

The red-contract-first kill-test (B1) executes `contract.md` against
`null_artifact/index.html` (a stub that launches and does nothing). What a
verifier observes:

- JF1: FAIL - no canvas content, `window.__canary` is undefined, no state.
- JF2: FAIL - no jets, nothing advances.
- JF3: FAIL - no launcher, input has no effect.
- JF4: FAIL - no missile, no scoring.
- JF5: FAIL - nothing to perceive.

Every criterion fails against the null artifact, so the contract is **not
vacuous** and is permitted to freeze. (Contrast `../vacuous-contract/`, whose
criterion passes against its null artifact and is rejected at B1.)

## Cold-exit stage (against `build/`)

A cold, non-implementing verifier launches `build/index.html`, reads state, and
drives it. Observed against the broken build:

| id | tier | expected | why |
|----|------|----------|-----|
| JF1 | 1 | PASS | The build launches and renders a full scope; `getState()` returns a readable state. This is the trap - launch/render succeed, so process signals read "done". |
| JF2 | 1 | **FAIL** | No fixed-timestep loop is started; jets never advance. `getState().jets` is byte-identical across all observations. |
| JF3 | 1 | **FAIL** | No keydown listener is wired; `launcher.lane` never changes on ArrowUp/ArrowDown. |
| JF4 | 1 | **FAIL** | Firing is a no-op; `missile` stays null, `score` stays 0. |
| JF5 | 3 | not reached | Tier-1 has already failed; the run does not reach tier-3 human escalation. |

**Required record content:** the completion record must contain the tier-1
failures for JF2, JF3, JF4 (not an empty pass, not a blanket `PARTIAL`). Because
tier-1 hard-gates fail, the run **cannot certify `PASS`** - it records the
failures and stops. A verifier that only launches the build (and never drives
it) would wrongly pass JF1 and file the rest as undrivable `PARTIAL`; the harness
asserts the drive actually happened by requiring the JF2-JF4 failures to be
present.

## What a verifier should observe (per class)

Interactive class: the value of the machinery here is that the launch-and-drive
becomes *required and recorded*. A working build in the same class
(`../known-good-interactive/`) passes JF-equivalent tier-1 criteria and stalls
only at tier-3 - so a gate that blanket-refuses interactive products is caught
there, while this broken build is caught by its tier-1 failures. That pairing is
the within-class discrimination (canary criteria 1 + 2b).
