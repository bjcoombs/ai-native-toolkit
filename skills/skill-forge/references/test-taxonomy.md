# Test-case taxonomy

The lead designs a small suite of test cases that the runners execute against the draft. A good suite spreads its cases across four types so the panel sees the skill from every angle a single happy-path run would miss.

Design **3-5 cases** for a forge, spanning the four types below. Not every type needs equal weight - a skill with a fragile trigger or a rationalization risk should lean adversarial; a skill meant to chain with others needs at least one composition case. When a new failure mode surfaces mid-run (a runner self-report exposes a gap no existing case covers), add a case for it to the corpus immediately, so the rest of the run and every future re-forge exercises it.

## The four case types

### Happy path
The skill used exactly as intended, on clean, in-scope input. Confirms the core function works at all before anything harder is asked of it. Design guide: pick the single most common real invocation; the input should need no special handling. If the happy path fails Fidelity, nothing else matters yet - fix that first.

### Edge case
A boundary or unusual-but-valid input: empty input, the largest plausible input, a value at the exact limit a step mentions, an input that satisfies the skill's preconditions in an unexpected way. Design guide: find every threshold, count, or "if X" branch in the draft and put one case on the boundary of each. Edge cases mostly feed Usability and Fidelity.

### Adversarial
Input designed to make the agent **rationalize its way out of following the skill** - the rationalization-escape hunt. Ambiguous instructions, a case where following the skill literally feels wrong, a "this rule obviously does not apply here" temptation, or input that invites the agent to improvise a shortcut. Design guide: read the draft for any soft instruction ("unless it seems unnecessary", "use judgment") and build a case that makes skipping it look reasonable. These feed the Adversarial lens via the runner's *improvisation* and *wanted to deviate* self-report fields (see `runner-prompt.md` and `judge-lenses.md`).

### Composition
The skill combined with another skill or concept - invoked alongside a second skill, or applied to input that is itself the output of another process. Design guide: pick the most likely real pairing and check the skill does not assume it runs alone (conflicting steps, duplicated work, contradictory output formats). Composition surfaces defects no single-skill case can.

## The persistent corpus

The corpus is **kept across re-forge runs**. It accumulates a skill's known failure modes, so re-forging the same skill later re-runs every case the skill has ever failed - the suite compounds over time the way the `.assess/` wiki does. A skill that has been forged three times carries the union of all three runs' hard cases, and a regression on any of them is caught immediately.

The corpus lives as a **sidecar in the target skill's forge directory** - alongside the skill being forged, not inside `skill-forge` itself and not at a hard-coded absolute path. The exact location is recorded in the forge report (see `forge-report-template.md`) so a later re-forge can find and reload it. In the standalone/chat path where there is no on-disk forge directory, the corpus is carried in the scratch artifact for the session.

Self-application is the deciding argument for persistence: forging the forge benefits directly from accumulating the meta-skill's own failure modes, so each self-forge starts harder than the last.
