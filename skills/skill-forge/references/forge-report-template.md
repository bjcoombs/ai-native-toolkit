# Forge report template

The forge report is the honest record of a forge run: what the skill was meant to do, how it was exercised, what changed each round, which gates it met, and whether it shipped. It ships alongside the hardened `SKILL.md`. A STOP report is as valid as a PROMOTE report - the value is the honest accounting, not a clean verdict.

Every finding row carries its `behavioural` / `static` tag (see `judge-lenses.md`): the four observational lenses produce `behavioural` findings, Trigger/routing produces `static` predictions. The fields below mirror the `panel-ledger.md` schema so the report can be rendered straight from the ledger.

Fill the template below. Replace every `<...>` with the run's actual values.

```markdown
# Forge report: <skill-name>

**Run date:** <date>  **Mode:** <team | phased | solo>  **Verdict:** <PROMOTE | STOP - best-so-far>

## Intent

The ground truth the Fidelity lens judged against. Derived clauses were marked
ASSUMED and accepted or rejected by the user before round 1.

| Clause | Status | Accepted by |
|--------|--------|-------------|
| <clause text> | confirmed / assumed-accepted / assumed-rejected | <user / supplied> |

Fidelity did not judge against any `assumed-rejected` clause.

## Test suite

| Case | Type | Input summary | Origin |
|------|------|---------------|--------|
| happy-1 | happy path | <...> | seed |
| edge-1 | edge case | <...> | seed |
| adv-1 | adversarial | <...> | seed |
| comp-1 | composition | <...> | seed |
| <id> | <type> | <...> | added round N |

Persistent corpus: <path to the sidecar in the target skill's forge directory>.

## Per-round log

One row per round. Hypothesis -> change -> result, straight from `amend_log`.

| Round | Lens that prompted it | Hypothesis (expect Z to improve) | Change made | Result |
|-------|----------------------|----------------------------------|-------------|--------|
| 1 | <lens> | <...> | <...> | improved / flat |
| 2 | <lens> | <...> | <...> | improved / flat |

## Gate ledger

Which gate was met, and in which round.

| Round | Gate 1 (Fidelity) | Gate 2 (no HIGH dissent) | Gate 3 (measurable gain) | Outcome |
|-------|-------------------|--------------------------|--------------------------|---------|
| 1 | fail / pass | - | - | iterate |
| 2 | pass | fail | pass | iterate |
| 3 | pass | pass | pass | PROMOTE |

## Dissent log

Severity-tagged, cumulative. Never suppressed - documented even when it did not
block. HIGH dissent blocks Gate 2; LOW/MED is recorded only.

| Round | Lens | Severity | Tag | Summary | Blocked a gate? |
|-------|------|----------|-----|---------|-----------------|
| 2 | adversarial | MED | behavioural | <...> | no |
| 3 | trigger-routing | HIGH | static | <...> | yes - Gate 2 |

## Final verdict

**<PROMOTE | STOP - best-so-far>**

- Gates met: <e.g. Gate 1, Gate 2, Gate 3>
- Gates not met: <none | which, and why>
- Residual HIGH-severity dissent: <none | summary> (required if STOP)
- Best-so-far artifact: <path to the hardened or best-reached SKILL.md>

## Rounds and waste

- Rounds run: <N>
- Budget ceiling: <max_rounds>
- Estimated waste: <rounds whose hypothesis came back flat, and why>
```
