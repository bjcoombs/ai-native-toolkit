# Gate hierarchy and promotion

A strict hierarchy, not a menu. The lead applies the gates in order each round. Dissent is always documented, never suppressed - a gate decision records why it was made, including the findings that did not block it.

The severities are one shared language across the whole system: `LOW`, `MED`, `HIGH`. The same `HIGH` bar that fails Gate 1 (a HIGH Fidelity finding) is the bar that blocks Gate 2 (a HIGH dissent). The lenses that produce these findings are defined in `judge-lenses.md`; the ledger that records them is `panel-ledger.md`.

| Gate | Bar | Effect |
|------|-----|--------|
| **Gate 1 - Objective** | Every test case passes the Fidelity judge | Hard. Any fail means not promotable - the lead must amend. Prevents standard-drift: a skill cannot ship while it still misbehaves on a case. |
| **Gate 2 - Panel confidence** | All cases green **and** no HIGH-severity dissent | LOW/MED dissent is documented but does not block. Only HIGH blocks. Catches "passes but weak" - a skill that scrapes through Fidelity while a lens is still raising a serious objection. |
| **Gate 3 - Diminishing returns** | The round produced measurable gain | No gain means stop coasting. Reads each lens's `round_verdict` (see below). |
| **Escape hatch - Budget** | Max rounds / token ceiling reached | Always terminates, with the best-so-far artifact and an honest "not promoted, here is why." |

## What "passes Fidelity" means (Gate 1)

Gate 1 is the hard gate, so this cannot be a vibe.

A test case **passes Fidelity** when the runner's output preserves the intent's core propositions with **no omission or distortion**. The intent clauses are the confirmed ones only - Fidelity never judges against a clause whose `intent[].status` is `assumed-rejected` (the ASSUMED guard; see `panel-ledger.md`).

- **Sub-HIGH Fidelity findings are advisory.** A LOW or MED Fidelity finding does not fail the case - it is recorded and may inform an amendment, but the case still passes Gate 1.
- **A HIGH-severity Fidelity finding is an automatic Gate-1 failure.** This is the same `HIGH` bar Gate 2 applies to dissent, so both gates speak one severity language.
- The bar is **propositional, not numeric.** "Did the output keep these propositions intact?" - not "did it score 8/10." Numeric scores on LLM judges are false precision.
- **Gate 1 is behavioural-only.** Static (Trigger/routing) findings never count toward Gate 1 - they block only at Gate 2 as dissent (see `judge-lenses.md`). A future edit tightening Gate 1 must not fold static findings in: Gate 1 reads Fidelity, and Fidelity alone.

## What "measurable gain" means (Gate 3)

The amendment's own logged hypothesis is the yardstick - nothing else.

Each AMEND logs "changed X because the `<lens>` found Y; expect Z to improve" into the `amend_log` (see `panel-ledger.md`, fields `change` and `hypothesis_metric`). Gate 3 registers gain **only if Z - the metric the hypothesis targeted - actually improved.** The signal is the targeted lens's `round_verdict`: a verdict of `better` on the metric the hypothesis named is a gain; `same` is not.

- A round whose targeted metric did not move is a **failed hypothesis** and counts as no gain, even if some unrelated lens happened to improve. Coincidental improvement elsewhere does not count.
- This is the same causal-attribution principle as one-change-per-round (credit a change only for the metric it targeted): credit the change for what it aimed at, not for coincidental drift.
- **Regressions are handled upstream, not here.** A round that makes a previously-passing case fail fails Gate 1; Gate 3 only ever asks whether the intended improvement landed.

## Promotion decision

**Promote if and only if Gate 1 passes AND Gate 2 passes.**

Otherwise **STOP** - either because Gate 3 reports no measurable gain (the loop has stopped improving) or because the budget ceiling is hit. A STOP ships:

- the **best-so-far** artifact (the highest-quality draft reached), and
- a report (see `forge-report-template.md`) that names **which gates were met and which were not**, and the residual HIGH-severity dissent that kept it from promoting.

A STOP is a valid v1 outcome, not a failure of the harness - the loop always terminates with useful, honest output. Dissent recorded along the way is always preserved in the report, never suppressed to make a gate look cleaner.

### STOP reason -> recommended next move

A STOP is never a dead end: it names *why* it stopped and *what to do next*. **The recommended next move is explicit in the report, not left for the user to infer.** The reason maps to a move:

| STOP reason | Recommended next move |
|-------------|-----------------------|
| **Gate 1 (Fidelity) unmet** - a case still fails Fidelity | Revise the skill substantively (the failing case names what behaviour is missing or distorted) and **re-forge**. A failing Gate 1 is not a ship-with-caveats outcome - the skill still misbehaves. |
| **Gate 2 HIGH dissent** - all cases pass but a HIGH finding stands | **Address the HIGH finding and re-forge**, OR **accept the best-so-far with the dissent documented** if the finding is a known, acceptable trade-off. Both are valid; the report records which was chosen. |
| **Budget hit** - max rounds / token ceiling reached before promotion | **Raise the budget and continue** if the per-round log shows the loop was still gaining, OR **accept the best-so-far** if rounds were coming back flat. Gate 3's `round_verdict` trend tells you which. |

The chosen move is written into the forge report's **Recommended Next Step** field (see `forge-report-template.md`), which is required on every STOP.
