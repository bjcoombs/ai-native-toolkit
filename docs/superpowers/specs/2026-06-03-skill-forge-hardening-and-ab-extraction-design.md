# Design: skill-forge hardening + A/B-equivalence extraction

Date: 2026-06-03
Status: Approved for implementation planning
Context: post-ship review of skill-forge (v1.28.4) against Anthropic's skill-eval guidance

## Problem

A fresh review of the shipped `skill-forge` (against Anthropic's "evals + test across Haiku/Sonnet/Opus" guidance) surfaced one architectural refactor and a set of hardening gaps. Verified against the shipped code, the genuinely-open items are below; items the review raised that are already handled (per-lens fixture calibration - the fixture plants one defect per lens; the batching threshold - already labelled provisional; corpus location - already in `test-taxonomy.md`) are out of scope.

This spec has two parts: **A) extract A/B-equivalence into its own library skill**, and **B) five hardening changes to skill-forge**.

## Part A: extract A/B-equivalence into its own library skill

A/B-equivalence currently lives inside `skill-forge` (`references/ab-equivalence.md` + `references/equivalence-judge-prompt.md`, ~216 lines), added when `semantic-compress` v2 needed it. The review's point stands: skill-forge answers *"is this one skill good?"* (quality panel -> promotion gate); A/B-equivalence answers *"do these two versions behave the same?"* - a different, more general question, with consumers beyond skill-forge (semantic-compress today; directive-clarity and prompt-regression next). Hosting it in skill-forge couples a general primitive to one skill's gate.

### Goal

A new library skill **`ab-equivalence`**: given two versions of a document and a transfer set, run each through a runner and judge per-case behavioural equivalence (`equivalent | candidate-regressed | candidate-diverged`) plus the efficiency signal. It is composed by `semantic-compress` (distill validation) and, later, `directive-clarity`.

### Runner layering (recommended)

The genuinely shared primitive is the **runner** ("apply a document to an input, return a transcript") - more general than the forge. So:

- **`ab-equivalence` owns** `runner-prompt.md` (moved from skill-forge) + the equivalence judge + the A/B orchestration.
- **`skill-forge` composes** `ab-equivalence`'s runner, and keeps its own 5-lens panel + 3-tier gate. Dependency flows by generality (skill-forge -> ab-equivalence), the same way `marathon` composes `pr-review-merge`.
- **`semantic-compress`** composes `ab-equivalence` for distill-mode validation (replacing its current dependency on skill-forge's A/B capability).

**Lighter fallback if the rewire is too much churn:** leave `runner-prompt.md` in skill-forge and have `ab-equivalence` reference it. This still removes the A/B orchestration + equivalence judge from skill-forge (the review's core ask) but leaves a mild `ab-equivalence -> skill-forge` dependency. The plan picks one after sizing the rewire; the recommended layering is preferred.

### Behaviour-preserving

This is a **refactor**: forge behaviour and distill behaviour must be identical before and after. The extraction is verified by re-running both (a behaviour-preserving move is exactly the optimizer's own domain - the meta-check is to A/B the forge against itself pre/post-extraction).

### Knock-on

`scripts/standalone_skill_config.py` + `scripts/tests/test_integration.py` gain an `ab-equivalence` entry/class; `skills/README.md`, `docs/index.md`, `.claude-plugin/plugin.json` updated; team/subagent infra `chat-skip`-wrapped as usual.

## Part B: skill-forge hardening (the open review items)

### B1 - Multi-model runner testing (highest value)

skill-forge fixes the runner model implicitly. A skill that passes Fidelity on Opus runners can fail on Haiku, and Anthropic's guidance is to test across tiers. Add:

- A **runner-model knob** (which model(s) the runner uses).
- A rule: **forge against the weakest tier the skill will ship to** (Haiku is where skills break); optionally sweep tiers for skills that ship broadly.
- The forge report records which model(s) the skill was forged against - a skill forged only on Opus is not certified for Haiku.

### B2 - Scale investment by skill scope (not by lens count)

The 5-lens default over-invests on a 50-line reference card. But the cost driver is **runners = test cases x rounds**, not lens count (the five lenses judge the *same* transcripts cheaply; Trigger is a static read). So:

- Scale the **test-suite size and round budget** by skill scope (lines / surface). A tiny skill gets a minimal suite (1-2 cases) and a tight round budget; a large skill gets the full taxonomy.
- **Keep all five lenses regardless of size** - dropping lenses by size would re-open the exact hole the self-forge closed (a 3-lens default silently drops Compression and Trigger). Lens count stays driven by explicit quick-check request, not by size.

### B3 - STOP -> next-steps workflow

On STOP (gate unmet at budget), skill-forge produces a best-so-far artifact + a report naming the unmet gate, but the *continuation* is implicit. Spell it out:

- **Gate 1 (Fidelity) unmet** -> the skill cannot reliably do its job: revise the skill substantively (or the intent, if a clause was wrong) and re-forge.
- **Gate 2 (HIGH dissent) unmet** -> a real quality risk remains: address the named HIGH finding and re-forge, or accept best-so-far with the dissent documented as a known limitation.
- **Budget hit mid-progress** -> raise the budget and continue, or accept best-so-far.
- Make the recommended next move explicit in the report, not left for the user to infer.

### B4 - Promote semantics (context-dependent)

"Promote" is currently vague about what writing the artifacts means across contexts. Make it explicit:

- **Plugin repo** (skill lives in a repo): write the hardened files in place on a branch and **offer a PR**, as `assess-pr` does.
- **Personal skill** (`~/.claude/skills/<name>/`): update the files **in place** (no PR), and say so.
- The forge report always lists exactly what was written and where.

### B5 - Borderline-severity calibration cases

The flawed fixture proves each lens *can* catch its planted defect, but not that the behavioural lenses are well-calibrated on *severity* (a systematic leniency bias would pass the gate while under-rating real findings). Extend the fixture with a few **near-miss / borderline cases** (a defect a lens should rate LOW/MED, and a clean case a lens should pass) so the calibration check stresses severity judgement, not just detection. This addresses the real residual behind the review's (mistaken) "only Trigger is calibrated" point.

## Non-goals

- No change to the gate hierarchy, the five lenses' definitions, or the behavioural/static rule.
- The 5-lens default stays; only suite size and round budget scale by scope.
- The A/B extraction changes no forge or distill *behaviour* - it is a pure refactor.

## Validation

- **Re-forge skill-forge** after both parts: it must still self-promote (the self-forge is its acceptance test).
- **Behaviour-preserving check on the extraction**: A/B the forge and the distill loop pre/post-extraction - same behaviour, by construction.
- Contract + standalone suites green; standalone ZIPs build.

## Housekeeping (fold in)

The v2 distillation design spec (`2026-06-03-semantic-compress-distillation-design.md`) and the directive-clarity follow-up spec (`2026-06-03-instruction-optimizer-directive-clarity-design.md`) currently live only on the `semantic-compress-v2` branch, not on `main` - the implementation marathon shipped the code without merging the design history. Restore both to `main`'s `docs/superpowers/specs/` (and the index) as part of this work so the design record is complete and the directive-clarity tag's tasks reference a spec that exists on main.

## File changes (for the plan)

- New: `skills/ab-equivalence/SKILL.md` + references (moved `runner-prompt.md`, `ab-equivalence.md`, `equivalence-judge-prompt.md`).
- `skills/skill-forge/SKILL.md` + references: remove the A/B-equivalence host content; compose `ab-equivalence`'s runner; add B1 multi-model, B2 scope-scaling (in `judge-lenses.md` / a runner section), B3 STOP next-steps, B4 promote semantics; extend the flawed fixture for B5.
- `skills/semantic-compress/SKILL.md` + `references/distill-loop.md`: compose `ab-equivalence` instead of skill-forge's A/B capability.
- `scripts/standalone_skill_config.py`, `scripts/tests/test_integration.py`: add `ab-equivalence`; update skill-forge/semantic-compress entries.
- `skills/README.md`, `docs/index.md`, `.claude-plugin/plugin.json` (MINOR bump), `CLAUDE.md` scope.
- Restore the two orphaned design specs to `main` + index.

## Open questions for the plan

- Runner layering: confirm the recommended (`ab-equivalence` owns the runner) vs the lighter fallback after sizing the skill-forge rewire.
- The multi-model knob: is it a forge-time argument, a Marathon-config value, or both? How does the standalone (solo, no model choice) path degrade?
- B2 scope thresholds: what line/surface counts map to minimal vs full suites - and is this a hard rule or lead judgement?
- Does extracting the runner break the standalone build for skill-forge (which `chat-skip`s its team infra)? Confirm the markers move correctly with the runner.
