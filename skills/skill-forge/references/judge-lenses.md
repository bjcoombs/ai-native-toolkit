# Judge lenses

The judge panel is up to five document-quality lenses. Two deterministic selectors apply, in order: the **artifact-type selector** fixes the full set for the target, then the **scope/quick-check selector** may narrow within it.

## Artifact-type selector (runs first)

The artifact type (detected by skill-forge's path/filename dispatch - see `SKILL.md`) sets the maximum lens set, because one lens is structurally meaningless for one type:

| Artifact type | Full lens set | Why |
|---------------|---------------|-----|
| **skill** (`SKILL.md`, default) | all 5 (Fidelity, Adversarial, Compression, Usability, Trigger/routing) | a skill is routed by its `description` / `TRIGGER` clause, so Trigger/routing has something to predict |
| **always-loaded instruction file** (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursor/rules/*`, `.github/copilot-instructions.md`) | 4 - **Trigger/routing dropped** | the file is loaded into every session unconditionally; it has no `TRIGGER` clause to mis-fire, so there is nothing for that lens to predict and prompt-injection cannot exercise it |

Dropping Trigger/routing here is **not** the silent under-running the scope selector forbids below: a lens with no defect class to own is removed because it cannot fire, not to save runner cost. The other four lenses map directly to an instruction file - their defect classes (intent omission, rationalization escape, bloat, follow-without-confusion) all apply.

## Scope / quick-check selector (runs second, within the type's set)

The panel scales by stakes, capped at the type's full set:

- **full set** - the default forge: every lens the type offers (5 for a skill, 4 for an instruction file). An unqualified `/skill-forge` run uses the full set, and a self-forge always uses the full set.
- **3 lenses** - an explicit quick check (Fidelity, Adversarial, Usability), only when the user asks for a faster or cheaper pass.
- **2 lenses** - a fast gut check (Fidelity + the highest-risk second lens for the target), only on explicit request.

**Deterministic selector:** default to the type's full set; drop to 3 or 2 only when the user explicitly requests a quick or cheap check. Never silently run fewer - dropping Compression (or, for a skill, Trigger/routing) means a whole defect class (bloat, or static routing) goes unchecked.

Confidence is **not** a lens. It is the stopping decision applied in Gate 2 (see `gate-hierarchy.md`), not an artifact perspective a judge holds. A lens reports findings and a per-round verdict; the lead-chair reads the panel's confidence off those, it is never scored as its own row.

## Scope-based test scaling

The **lens count is fixed by artifact type** (5 for a skill, 4 for an instruction file); what scales with document size is the **number of test cases** and the **round budget** - the two multipliers that actually drive runner cost. The scope metric is the document's lines / surface area (for a skill, count the `SKILL.md` plus the reference files an agent must load to act; for an instruction file, the file itself). The thresholds below are **lead-judgement starting points, not hard rules** - a 60-line skill with a fragile trigger may still warrant a Medium suite.

| Scope | Size | Test cases | Round budget |
|-------|------|------------|--------------|
| **Small** | < 100 lines | 1-2 cases (1 happy-path + 1 adversarial) | 3-round |
| **Medium** | 100-300 lines | 3-4 cases | 5-round |
| **Large** | > 300 lines | full 3-5 per taxonomy type | 7-round |

**The type's full lens set runs regardless of scope - dropping lenses by size re-opens the exact hole the self-forge closed** (the 5-lens-promise-undercut-by-a-3-lens-default HIGH that round 1 of the bootstrap caught). Scope scales test cases and rounds; it never scales the panel. A small target gets fewer runners, not fewer lenses.

## The lenses

| Lens | Judges | Defect class it owns |
|------|--------|----------------------|
| **Fidelity** | Did the runner's behaviour match the stated intent? | The core pass/fail. Document says X, the agent did Y. Omission or distortion of the intent's propositions. For an instruction file, includes the **accuracy sub-check** below. |
| **Adversarial** | Can it be broken - by ambiguous input, boundary cases, rationalization escapes, or a later edit? | Rationalization holes and **future-edit safety**: maintainability folds in here as an attack vector. A document that stays correct only until someone edits it is breakable. |
| **Compression** | Is it as short as it can be? | Bloat, redundancy, denormalized training knowledge - prose that explains what the model already knows ("point, don't paste") instead of instructing. |
| **Usability** | Could a fresh agent follow it without confusion? | Ordering defects, missing steps, contradictions, references to context the document never establishes. |
| **Trigger/routing** (skill only) | Will the router fire it on the right phrases and not over-fire? | A bug class no other lens sees - whether the `description` / `TRIGGER` clause selects the skill correctly. The one thing prompt-injection cannot behaviourally test. Not present for an instruction file (no `TRIGGER` clause to route on). |

### Fidelity's accuracy sub-check (instruction files)

For an always-loaded instruction file, Fidelity carries an explicit **accuracy** sub-check on top of the propositional intent check: **every command and path the file states must actually exist and match what the repo's tooling enforces.** An instruction file's worst failure mode is not vagueness but a *confident, wrong* statement - a command that no longer exists, a path that moved, or a checklist that names a strict subset of what a validator actually enforces. A cold-start agent trusting such a file does the stated thing and breaks the build, because the file was a self-consistent but inaccurate map of the repo.

The sub-check reads the runner's *output produced* and *ambiguities hit* fields (the read-only runner records which stated commands/paths it found, and which it could not resolve) and cross-checks them against the repo's real tooling:

- A stated command or path that **does not exist** is an accuracy failure. Severity tracks blast radius: a broken convenience alias is LOW/MED; a stated build/test/commit step that would fail or be skipped is **HIGH** (an automatic Gate-1 failure - see `gate-hierarchy.md`).
- A checklist or step that names a **strict subset** of the surfaces the repo's validator enforces is a HIGH accuracy failure - the **count-surface trap**: the agent updates the listed surfaces, the unlisted-but-enforced surface stays stale, and CI fails. Completeness against the enforcing tool, not internal consistency, is the bar.
- Accuracy is **behavioural**, not static: it is grounded in what the runner found when it tried to act on the file against the real repo, not in reading the file in isolation. It therefore gates at Gate 1 like the rest of Fidelity.

## Behavioural vs static evidence

Four lenses - Fidelity, Adversarial, Compression, Usability - judge **runner transcripts** (observed behaviour), so their findings are tagged `behavioural`. Trigger/routing judges the **skill text directly**: prompt-injection hands the draft to the runner as its instructions, so the runner always runs the skill and injection can never make a `TRIGGER` clause mis-fire the way a live router would. Its findings are therefore **predictions**, tagged `static`. The flawed fixture calibrates this lens's *reading* of the `description`, not a behavioural observation.

**The fixture calibrates both detection and severity.** Beyond the one planted defect per lens (detection), the flawed fixture carries borderline cases with expected severities (a Borderline-LOW and a Borderline-MED) plus a clean-pass and a near-miss case that must draw no finding (see the fixture's `DEFECTS.md`). A lens that detects every defect but mis-rates its severity - or fires on the clean-pass / near-miss case - is miscalibrated even when detection is perfect. Rating, not just finding, is part of what the fixture proves.

Two consequences follow:

- The forge report tags every finding `behavioural` or `static` (see `forge-report-template.md`).
- **Trigger/routing findings never gate Gate 1** (behavioural-only); a HIGH-severity Trigger prediction blocks only at **Gate 2**, as panel dissent. LOW/MED Trigger predictions are documented, not blocking.

## What each lens reads in the self-report

Every runner returns a self-report in the format defined in ab-equivalence's runner prompt - skill-forge composes that runner rather than owning one (`../../ab-equivalence/references/runner-prompt.md`, relative to this file). Each behavioural lens reads specific fields:

- **Fidelity** reads the *output produced* and compares it against the confirmed intent clauses (it ignores any clause whose `intent[].status` is `assumed-rejected` - see `panel-ledger.md`). For an instruction file it additionally reads *output produced* and *ambiguities hit* for the **accuracy sub-check** above - the stated commands/paths the runner found or could not resolve against the real repo.
- **Usability** reads *steps followed / skipped and why* - a step skipped because it was unclear or unreachable is a usability defect, not a runner failure.
- **Adversarial** reads *improvisation beyond the skill* and *any point it wanted to deviate but followed literally* - these are the rationalization-escape signals: where the skill let the agent talk itself out of following it, or only held because the runner was disciplined.
- **Compression** reads the *output* and the draft together - effort spent on prose the runner never needed to act on is bloat.
- **Trigger/routing** does not read the self-report at all - it reads the draft's `description` / `TRIGGER` clause directly, which is why its findings are `static`.

*Ambiguities hit and how they were resolved* feed both Usability (the ambiguity exists) and Adversarial (the resolution could be wrong under a hostile reading).
