# Test-case taxonomy

The lead designs a small suite of test cases that the runners execute against the draft. A good suite spreads its cases across four types so the panel sees the skill from every angle a single happy-path run would miss.

Design **3-5 cases** for a forge, spanning the four types below. Not every type needs equal weight - a skill with a fragile trigger or a rationalization risk should lean adversarial; a skill meant to chain with others needs at least one composition case. When a new failure mode surfaces mid-run (a runner self-report exposes a gap no existing case covers), add a case for it to the corpus immediately, so the rest of the run and every future re-forge exercises it.

### Minimal suites for small skills

The 3-5-case default is the Medium/Large suite; it scales down by scope (see the scope-scaling table in `judge-lenses.md`). **A 50-line reference card needs 1 happy-path and 1 adversarial case, not the full taxonomy** - the edge and composition types are optional when the skill has no branches and no intended pairing. What does **not** scale down is the panel: the **five-lens default stays; only the test-case count and the round ceiling scale** with size. A small suite means fewer runners, never fewer lenses.

## The four case types

### Happy path
The skill used exactly as intended, on clean, in-scope input. Confirms the core function works at all before anything harder is asked of it. Design guide: pick the single most common real invocation; the input should need no special handling. If the happy path fails Fidelity, nothing else matters yet - fix that first.

### Edge case
A boundary or unusual-but-valid input: empty input, the largest plausible input, a value at the exact limit a step mentions, an input that satisfies the skill's preconditions in an unexpected way. Design guide: find every threshold, count, or "if X" branch in the draft and put one case on the boundary of each. Edge cases mostly feed Usability and Fidelity.

### Adversarial
Input designed to make the agent **rationalize its way out of following the skill** - the rationalization-escape hunt. Ambiguous instructions, a case where following the skill literally feels wrong, a "this rule obviously does not apply here" temptation, or input that invites the agent to improvise a shortcut. Design guide: read the draft for any soft instruction ("unless it seems unnecessary", "use judgment") and build a case that makes skipping it look reasonable. These feed the Adversarial lens via the runner's *improvisation* and *wanted to deviate* self-report fields (see ab-equivalence's composed runner at `../../ab-equivalence/references/runner-prompt.md`, relative to this file, and `judge-lenses.md`).

### Composition
The skill combined with another skill or concept - invoked alongside a second skill, or applied to input that is itself the output of another process. Design guide: pick the most likely real pairing and check the skill does not assume it runs alone (conflicting steps, duplicated work, contradictory output formats). Composition surfaces defects no single-skill case can.

## Instruction-file mapping: cases are realistic repo tasks

When the target is an always-loaded instruction file, a "test case" is not an invocation input - it is a **realistic repo task** the read-only runner carries out using only that file as context (see the instruction-file runner variant in `../../ab-equivalence/references/runner-prompt.md`, relative to this file). The four case types map directly to task shapes:

| Case type | Instruction-file task shape | What it exercises |
|-----------|-----------------------------|-------------------|
| **Happy path** | The single most common routine task the repo expects (e.g. "add a small feature and prepare it for commit") done by the book | Fidelity (cold-start agent is productive from this file alone) and the accuracy of the file's core commands/paths |
| **Edge case** | A task at a boundary the file mentions (an empty/edge input, a step's exact limit, an unusual-but-valid repo state) | Usability and Fidelity - whether a stated branch or threshold actually holds |
| **Adversarial** | A task that tempts a **build-breaking shortcut** - including the **trap case** below | Adversarial (rationalization escape) and Fidelity's accuracy sub-check |
| **Composition** | A task spanning two of the repo's conventions at once (e.g. a change that touches both the migration rule and the commit rule) so contradictions between sections surface | Usability and Adversarial - whether the file's rules conflict when combined |

### The trap case (mandatory for an instruction file)

The adversarial slot **must** include at least one trap that tempts a build-breaking shortcut the file's own wording makes look reasonable. The canonical trap is the **count-surface trap**: the repo's validator enforces a count across N surfaces, but the instruction file's checklist names a strict subset of them. A runner trusting the checklist updates the listed surfaces, leaves an enforced-but-unlisted surface stale, and CI fails - exactly the HIGH accuracy defect Fidelity's accuracy sub-check owns (see `judge-lenses.md`). The `flawed-instruction-file` fixture (`tests/fixtures/flawed-instruction-file/`) plants this trap against a synthetic validator so the case is reproducible without hand-holding; its `DEFECTS.md` is the answer key. Other build-breaking traps follow the same shape: a stated command that no longer exists, a path that moved, a "skip the slow test, it always passes" temptation that the CI gate does not skip.

## The persistent corpus

The corpus is **kept across re-forge runs**. It accumulates a skill's known failure modes, so re-forging the same skill later re-runs every case the skill has ever failed. A skill that has been forged three times carries the union of all three runs' hard cases, and a regression on any of them is caught immediately.

Each case records whether it was a `seed` case (designed at the start of a run) or an `added round N` case (born from a failure mode surfaced mid-run) - the same vocabulary the test-suite table uses in `forge-report-template.md`.

The corpus lives as a **sidecar in the target's forge directory** - alongside the document being forged (a skill or an instruction file), not inside `skill-forge` itself and not at a hard-coded absolute path. The exact location is recorded in the forge report (see `forge-report-template.md`) so a later re-forge can find and reload it. In the standalone/chat path where there is no on-disk forge directory, the corpus is carried in the scratch artifact for the session.

Self-application is the deciding argument for persistence: forging the forge benefits directly from accumulating the meta-skill's own failure modes, so each self-forge starts harder than the last.
