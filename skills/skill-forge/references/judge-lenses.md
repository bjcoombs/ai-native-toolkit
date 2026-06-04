# Judge lenses

The judge panel is five skill-quality lenses. The panel scales by stakes, mirroring huddle's Fibonacci team sizing but capped at these five:

- **5 lenses** - the default forge: all of the below. An unqualified `/skill-forge` run uses all five, and a self-forge always uses all five.
- **3 lenses** - an explicit quick check (Fidelity, Adversarial, Usability), only when the user asks for a faster or cheaper pass.
- **2 lenses** - a fast gut check (Fidelity + the highest-risk second lens for the skill), only on explicit request.

**Deterministic selector:** default to all 5; drop to 3 or 2 only when the user explicitly requests a quick or cheap check. Never silently run fewer than 5 - dropping Compression or Trigger/routing means a whole defect class (bloat, or static routing) goes unchecked.

Confidence is **not** a lens. It is the stopping decision applied in Gate 2 (see `gate-hierarchy.md`), not an artifact perspective a judge holds. A lens reports findings and a per-round verdict; the lead-chair reads the panel's confidence off those, it is never scored as its own row.

## Scope-based test scaling

The **lens count is fixed at five**; what scales with skill size is the **number of test cases** and the **round budget** - the two multipliers that actually drive runner cost. The scope metric is the skill's lines / surface area (count the `SKILL.md` plus the reference files an agent must load to act). The thresholds below are **lead-judgement starting points, not hard rules** - a 60-line skill with a fragile trigger may still warrant a Medium suite.

| Scope | Size | Test cases | Round budget |
|-------|------|------------|--------------|
| **Small** | < 100 lines | 1-2 cases (1 happy-path + 1 adversarial) | 3-round |
| **Medium** | 100-300 lines | 3-4 cases | 5-round |
| **Large** | > 300 lines | full 3-5 per taxonomy type | 7-round |

**All five lenses run regardless of scope - dropping lenses by size re-opens the exact hole the self-forge closed** (the 5-lens-promise-undercut-by-a-3-lens-default HIGH that round 1 of the bootstrap caught). Scope scales test cases and rounds; it never scales the panel. A small skill gets fewer runners, not fewer lenses.

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

**The fixture calibrates both detection and severity.** Beyond the one planted defect per lens (detection), the flawed fixture carries borderline cases with expected severities (a Borderline-LOW and a Borderline-MED) plus a clean-pass and a near-miss case that must draw no finding (see the fixture's `DEFECTS.md`). A lens that detects every defect but mis-rates its severity - or fires on the clean-pass / near-miss case - is miscalibrated even when detection is perfect. Rating, not just finding, is part of what the fixture proves.

Two consequences follow:

- The forge report tags every finding `behavioural` or `static` (see `forge-report-template.md`).
- **Trigger/routing findings never gate Gate 1** (behavioural-only); a HIGH-severity Trigger prediction blocks only at **Gate 2**, as panel dissent. LOW/MED Trigger predictions are documented, not blocking.

## What each lens reads in the self-report

Every runner returns a self-report in the format defined in ab-equivalence's runner prompt - skill-forge composes that runner rather than owning one (`../../ab-equivalence/references/runner-prompt.md`, relative to this file). Each behavioural lens reads specific fields:

- **Fidelity** reads the *output produced* and compares it against the confirmed intent clauses (it ignores any clause whose `intent[].status` is `assumed-rejected` - see `panel-ledger.md`).
- **Usability** reads *steps followed / skipped and why* - a step skipped because it was unclear or unreachable is a usability defect, not a runner failure.
- **Adversarial** reads *improvisation beyond the skill* and *any point it wanted to deviate but followed literally* - these are the rationalization-escape signals: where the skill let the agent talk itself out of following it, or only held because the runner was disciplined.
- **Compression** reads the *output* and the draft together - effort spent on prose the runner never needed to act on is bloat.
- **Trigger/routing** does not read the self-report at all - it reads the draft's `description` / `TRIGGER` clause directly, which is why its findings are `static`.

*Ambiguities hit and how they were resolved* feed both Usability (the ambiguity exists) and Adversarial (the resolution could be wrong under a hostile reading).
