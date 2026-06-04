# The panel ledger

In team mode the judges persist across rounds and remember prior findings natively. In phased and solo modes the judge subagents have **fresh contexts** - they remember nothing - so a prose synopsis cannot carry the load. The panel ledger is a structured object, injected into every judge spawn, that lets the panel remember prior rounds without persistent agents.

**The panel ledger and the crash-recovery round-tracking JSON are the same object** - one persisted file in the scratch directory that both survives a crash and feeds the next round's judges.

## Schema

```json
{
  "meta": {"target_skill": "<name>", "round": 3, "mode": "phased", "budget": {"max_rounds": 8, "rounds_spent": 3}},
  "intent": [{"clause": "...", "status": "confirmed|assumed-rejected|assumed-accepted"}],
  "lenses": {
    "fidelity": {
      "round_verdict": "better|same|worse",
      "findings": [{"case": "edge-1", "severity": "HIGH", "summary": "...", "kind": "behavioural"}]
    }
  },
  "dissent": [{"lens": "adversarial", "severity": "MED", "summary": "...", "round": 2}],
  "amend_log": [{"round": 2, "change": "...", "hypothesis_metric": "...", "result": "improved|flat"}]
}
```

### Fields

Notes beyond the schema above - enum values and which gate reads each field:

- `meta.mode` - one of `team` / `phased` / `solo`. `meta.budget` (`max_rounds`, `rounds_spent`) is what the budget escape hatch reads.
- `intent[].status` - `confirmed`, `assumed-accepted`, or `assumed-rejected`; enforces the ASSUMED guard (Fidelity ignores `assumed-rejected` clauses).
- `lenses` - one entry per active lens (`fidelity`, `adversarial`, `compression`, `usability`, `trigger-routing`). `round_verdict` is `better` / `same` / `worse`; `findings[].severity` is `LOW` / `MED` / `HIGH` and `findings[].kind` is `behavioural` for the four observational lenses, `static` for `trigger-routing`.
- `dissent[]` - cumulative and severity-tagged; a `HIGH` entry blocks Gate 2.
- `amend_log[].result` - `improved` / `flat`; Gate 3 reads gain off the `round_verdict` of the lens named by `hypothesis_metric`.

## Worked example

A round-3 ledger for a phased forge of a `commit-writer` skill, mid-run:

```json
{
  "meta": {
    "target_skill": "commit-writer",
    "round": 3,
    "mode": "phased",
    "budget": {"max_rounds": 8, "rounds_spent": 3}
  },
  "intent": [
    {"clause": "Output a conventional-commit message of the form type(scope): subject", "status": "confirmed"},
    {"clause": "Always append a tracker reference to the subject", "status": "assumed-rejected"},
    {"clause": "Keep the subject imperative and under 50 characters", "status": "assumed-accepted"}
  ],
  "lenses": {
    "fidelity": {
      "round_verdict": "better",
      "findings": [
        {"case": "happy-1", "severity": "LOW", "summary": "subject phrased past-tense not imperative", "kind": "behavioural"}
      ]
    },
    "adversarial": {
      "round_verdict": "same",
      "findings": [
        {"case": "adv-1", "severity": "MED", "summary": "runner talked itself past the 50-char rule on a 'losing meaning' pretext", "kind": "behavioural"}
      ]
    },
    "trigger-routing": {
      "round_verdict": "better",
      "findings": [
        {"case": "n/a", "severity": "HIGH", "summary": "description has no TRIGGER clause and over-fires on any text task", "kind": "static"}
      ]
    }
  },
  "dissent": [
    {"lens": "adversarial", "severity": "MED", "summary": "rationalization escape on the char limit persists", "round": 2},
    {"lens": "trigger-routing", "severity": "HIGH", "summary": "over-broad description predicts router over-fire", "round": 3}
  ],
  "amend_log": [
    {"round": 2, "change": "rewrote step 3 to require type(scope): subject format", "hypothesis_metric": "fidelity", "result": "improved"},
    {"round": 3, "change": "added imperative-mood requirement to subject", "hypothesis_metric": "fidelity", "result": "improved"}
  ]
}
```

## How the gates read the ledger

These reads are the contract between the ledger and `gate-hierarchy.md`:

- **`round_verdict` per lens is what Gate 3 reads.** Gate 3 registers measurable gain only when the lens named by the round's `amend_log[].hypothesis_metric` has a `round_verdict` of `better`. In the example, round 3's amendment targeted `fidelity`, and `fidelity.round_verdict` is `better`, so Gate 3 sees gain.
- **A `dissent[]` entry with `severity: HIGH` blocks Gate 2.** In the example the `trigger-routing` HIGH dissent blocks promotion at Gate 2 even though every case is green - "passes but weak." The MED `adversarial` dissent is documented but does not block.
- **`intent[].status` enforces the ASSUMED guard.** Fidelity ignores any clause whose `status` is `assumed-rejected` - in the example, the "always append a tracker reference" clause was a derived assumption the user rejected, so Fidelity does not penalize the runner for omitting it.
