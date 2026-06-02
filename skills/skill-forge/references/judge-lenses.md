# Judge lenses

The judge panel is five skill-quality lenses, repointed from `huddle`'s professional lenses at the question "does this skill behave?". The panel scales by stakes, mirroring huddle's Fibonacci team sizing but capped at these five:

- **2 lenses** - a quick check (Fidelity + the highest-risk second lens for the skill).
- **3 lenses** - the default forge (Fidelity, Adversarial, Usability).
- **5 lenses** - a deep forge (all of the below).

Confidence is **not** a lens. It is the stopping decision applied in Gate 2 (see `gate-hierarchy.md`), not an artifact perspective a judge holds. A lens reports findings and a per-round verdict; the lead-chair reads the panel's confidence off those, it is never scored as its own row.

## The five lenses

| Lens | Judges | Defect class it owns |
|------|--------|----------------------|
| **Fidelity** | Did the runner's behaviour match the stated intent? | The core pass/fail. Skill says X, the agent did Y. Omission or distortion of the intent's propositions. |
| **Adversarial** | Can it be broken - by ambiguous input, boundary cases, rationalization escapes, or a later edit? | Rationalization holes and **future-edit safety**: maintainability folds in here as an attack vector. A skill that stays correct only until someone edits it is breakable. |
| **Compression** | Is it as short as it can be? | Bloat, redundancy, denormalized training knowledge - prose that explains what the model already knows ("point, don't paste") instead of instructing. |
| **Usability** | Could a fresh agent follow it without confusion? | Ordering defects, missing steps, contradictions, references to context the skill never establishes. |
| **Trigger/routing** | Will the router fire it on the right phrases and not over-fire? | A bug class no other lens sees - whether the `description` / `TRIGGER` clause selects the skill correctly. The one thing prompt-injection cannot behaviourally test. |

## Behavioural vs static evidence

Four lenses - Fidelity, Adversarial, Compression, Usability - judge **runner transcripts** (observed behaviour), so their findings are tagged `behavioural`. Trigger/routing judges the **skill text directly**: prompt-injection hands the draft to the runner as its instructions, so the runner always runs the skill and injection can never make a `TRIGGER` clause mis-fire the way a live router would. Its findings are therefore **predictions**, tagged `static`. The flawed fixture calibrates this lens's *reading* of the `description`, not a behavioural observation.

Two consequences follow:

- The forge report tags every finding `behavioural` or `static` (see `forge-report-template.md`).
- **Trigger/routing findings never gate Gate 1** (behavioural-only); a HIGH-severity Trigger prediction blocks only at **Gate 2**, as panel dissent. LOW/MED Trigger predictions are documented, not blocking.

## What each lens reads in the self-report

Every runner returns a self-report in the format defined in `runner-prompt.md`. Each behavioural lens reads specific fields:

- **Fidelity** reads the *output produced* and compares it against the confirmed intent clauses (it ignores any clause whose `intent[].status` is `assumed-rejected` - see `panel-ledger.md`).
- **Usability** reads *steps followed / skipped and why* - a step skipped because it was unclear or unreachable is a usability defect, not a runner failure.
- **Adversarial** reads *improvisation beyond the skill* and *any point it wanted to deviate but followed literally* - these are the rationalization-escape signals: where the skill let the agent talk itself out of following it, or only held because the runner was disciplined.
- **Compression** reads the *output* and the draft together - effort spent on prose the runner never needed to act on is bloat.
- **Trigger/routing** does not read the self-report at all - it reads the draft's `description` / `TRIGGER` clause directly, which is why its findings are `static`.

*Ambiguities hit and how they were resolved* feed both Usability (the ambiguity exists) and Adversarial (the resolution could be wrong under a hostile reading).
