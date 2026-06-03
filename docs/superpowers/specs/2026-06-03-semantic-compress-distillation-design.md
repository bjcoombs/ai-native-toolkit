# Design: `semantic-compress` v2 - holistic, A/B-validated distillation

Date: 2026-06-03
Status: Approved for implementation planning

## Problem

`semantic-compress` v1 is a **local, textual** operation: find a span that explains a concept the model already knows, replace it with a pointer. Tested across four corpora (this toolkit, Matt Pocock's skills, an algorithms README, the gstack giants), it is precise but near-noop in practice - the "long-form-unnamed concept" pattern barely occurs in deliberately-authored documents, and the operation cannot, by construction, compress a whole document: you cannot preserve a global, behavioural property with local text edits.

The actual need is **holistic** compression: make the document itself smaller while keeping what it *does*. And the essence of a document directed at an LLM is **behavioural, not textual** - the behaviour it induces in the reading model across the tasks it is meant to handle. You cannot tell by reading whether a smaller version preserved that behaviour, because introspection is biased: asked "will this behave the same?", the model guesses optimistically. The only ground truth is **A/B behavioural testing** - run the original and the candidate on the same inputs and compare what they actually do.

This was validated empirically before this spec: an aggressive ~50% distillation of a TDD skill, A/B-tested, preserved all five of its disciplines (and faithfully preserved its gaps) - and we could not have known that without running it. The cut text was behaviourally inert; only the A/B proved it.

## Goal

Expand `semantic-compress` into a **holistic distillation** capability: produce the smallest document that behaves the same as the original, with acceptance gated on **A/B behavioural equivalence** measured by `skill-forge`. The local core->pointer move is retained as an inner micro-operation; the headline becomes the distill loop.

## Non-goals

- **Not removing the local mode.** v1's span-level core->pointer stays as an inner move.
- **Not a new skill.** This expands `semantic-compress` (the user's explicit choice); it does not add a third skill to the family.
- **Not model training.** "Distillation" is the conceptual analogue (context/prompt distillation: a smaller artifact that reproduces behaviour over a transfer set). No model weights are touched.
- **Not absolute-quality forging.** The gate is equivalence-to-the-original, not "is this skill good" - that is `skill-forge`'s existing job. A faithful distillation preserves the original's flaws; improving the skill is a separate `skill-forge` run.

## Core principle (encoded as a hard rule)

**A compression is never accepted on inspection - only on behavioural evidence from an A/B run.** Introspection about behaviour is structurally unreliable; execution is the only arbiter. The skill must refuse to output a compressed document that has not passed an A/B equivalence run against the original.

## Architecture

`semantic-compress` drives the loop and **composes `skill-forge`** for the behavioural test, exactly as `marathon` composes `pr-review-merge`. Two roles split cleanly:

- **`semantic-compress` owns compression**: deriving/confirming the transfer set, regenerating smaller candidates, the iterate-to-minimal loop, the output report.
- **`skill-forge` owns behavioural testing**: it gains one thin new capability - **A/B equivalence** - that runs a runner over the same transfer set for two document versions and judges, per case, whether the candidate's behaviour is equivalent to the original's.

### The distill loop (iterative-to-minimal)

1. **Define the transfer set.** Derive behavioural test cases from the document across the `skill-forge` taxonomy (happy / edge / adversarial / composition). The user confirms or augments them before the baseline run - the transfer set *is* the operational definition of the essence, so the user signs off on it. The skill flags thin coverage.
2. **Capture the teacher.** Run the **original** document through `skill-forge`'s runner on each case; record the behaviour it induces (the disciplines enforced, the outputs produced). This baseline is the equivalence target.
3. **Compress = regenerate, not edit.** Produce a smaller candidate against the behavioural spec: the local core->pointer move, plus de-duplication and dropping behaviourally-inert prose. Regeneration (rewrite to function), not trimming, is what lets it get genuinely smaller.
4. **A/B validate.** Run the **candidate** through the runner on the same transfer set; the equivalence judge compares candidate-vs-teacher behaviour per case under **strict no-regression**.
5. **On any regression, the divergence names what was load-bearing.** Add back the minimum that restores the lost behaviour; re-validate.
6. **Converge** on the smallest candidate that still passes; stop at diminishing returns (a round that can only shrink further by regressing) or a budget ceiling.
7. **Output** the minimal equivalent document plus an **A/B report**.

### Strict no-regression gate

The candidate **passes** iff, on every transfer-set case, it induces every behaviour/discipline the original induced. Any dropped behaviour is a **fail** (essence lost) and triggers add-back. Incidental improvements (the candidate behaves *better* on a case) are acceptable but never required and never the goal - the goal is faithful, smaller reproduction. The gate is behavioural equivalence, not textual similarity.

### Distribution-shift guard

A distillation is only valid over the transfer set it was tested against - the same overfitting risk model distillation has outside its transfer distribution. The skill states this honestly and mitigates it two ways: push for diverse/adversarial coverage (breadth of the transfer set bounds the safety of the compression), and stay **conservative** - keep anything not *proven* behaviourally inert by the A/B. Silence about untested behaviour is forbidden; the report names the coverage.

## The `skill-forge` addition: A/B equivalence

A thin new capability, distinct from the existing absolute-quality panel:

- **Input:** two document versions (original = teacher, candidate = student) and a transfer set.
- **Mechanism:** for each case, run the existing runner (the pure-wrapper runner prompt) once per version, producing two transcripts. An **equivalence judge** compares the two transcripts and returns one of `equivalent | candidate-regressed | candidate-diverged`, citing the specific behaviour gained or lost.
- **Output:** a per-case equivalence verdict; pass = zero `candidate-regressed`. `candidate-diverged` (different but not worse) is documented for the user's judgement.
- **Transform-agnostic + efficiency signal.** The capability judges *behavioural equivalence between two versions* - it does not know or care which transform produced the candidate, so it serves every optimizer transform, not just compression. Alongside the equivalence verdict it records an **efficiency signal** per case (how directly the runner acted vs how much it had to unpack/reinterpret the instruction). Some transforms claim *behaviour-preserving-but-lighter* (the next one, directive-clarity) and can only be validated if the harness measures the lightness, not just the sameness: compression's gate is strict no-regression, while a lightness-claiming transform's gate is no-regression **and** a measured efficiency gain.
- **Reuse:** the runner and runner-prompt are unchanged. The equivalence judge is a new, focused judge (compare-two-transcripts), separate from the five quality lenses. It is a library capability `semantic-compress` invokes; it does not change `skill-forge`'s own gate hierarchy.

## Modes (when each fires)

- **Local mode** (v1, retained): a quick span-level core->pointer pass, no A/B - for trivially compressing a short prompt where behavioural risk is negligible.
- **Distill mode** (new, default for whole documents): the full A/B-validated loop above. Fires when compressing a skill/system-prompt/instruction document where preserving behaviour matters.

The skill selects deterministically: a whole document / skill / system prompt -> distill mode; a short snippet with an obvious local swap and no behavioural surface -> local mode is permitted, but distill is the safe default.

## Artifacts

- The **minimal equivalent document**.
- An **A/B distillation report**: original vs final size delta; the transfer set and its coverage; per-case equivalence verdicts; what was dropped (proven behaviourally inert); what proved load-bearing and was kept or added back; rounds run; the distribution-shift caveat (behaviour outside the transfer set is not guaranteed).

## File changes (for the plan)

- `skills/semantic-compress/SKILL.md` - reframe to two modes; add the distill loop, the hard behavioural-evidence rule, the distribution-shift guard, mode selection.
- `skills/semantic-compress/references/` - distill-loop detail, transfer-set design guide (reuse taxonomy), the A/B distillation report template.
- `skills/skill-forge/` - add the A/B-equivalence capability (a reference doc + the equivalence-judge prompt + a one-line mode entry in `SKILL.md`); `semantic-compress` invokes it.
- `scripts/standalone_skill_config.py` + `scripts/tests/test_integration.py` - update the semantic-compress standalone entry/tests if references change; A/B/runner infra is `chat-skip` (team/subagent infra) for the standalone build, mirroring how skill-forge handles it.
- `skills/README.md`, `docs/index.md`, `.claude-plugin/plugin.json` (description + MINOR version bump), `CLAUDE.md` scope if wording changes.

## Validation

This expansion is itself a `skill-forge` candidate. Once built, forge the expanded `semantic-compress`; and as a real-world acceptance test, run the new distill mode on a genuinely bloated document (a gstack giant such as `office-hours`, ~16k words) and confirm it produces a meaningfully smaller version that passes strict no-regression A/B - the case v1 could not touch.

## The optimizer: a family of behaviour-preserving transforms

Compression is the **first** of a family of behaviour-preserving instruction transforms this skill will host - each one making an LLM-directed document *lighter* (cheaper to read, faster to act on) without changing what it does, every one gated by the same A/B harness. The family is sourced from a cognitive-ergonomics view of instruction quality (negation cost, cognitive load, goal clarity, consistency - the way a document either fits or fights how the model processes it).

- **Transform #1 - compress** (this spec): point at core, drop behaviourally-inert prose. Gate = strict no-regression.
- **Transform #2 - directive-clarity** (follow-up spec: `2026-06-03-instruction-optimizer-directive-clarity-design.md`): rewrite instructions that force the model to *unpack* an action (bare negations, facts-not-actions, vague pointers, ordering rules) into concrete directives that name the action. Gate = no-regression **and** measured efficiency gain. An A/B preview established the lever is *directive clarity*, not positive polarity - so this is a tested hypothesis, not an assumed rule.

Each future transform is a **hypothesis, A/B-validated before it ships** - the harness keeps the family honest. The umbrella stays distinct from `skill-forge`: the optimizer **refactors** (behaviour preserved, form lighter); `skill-forge` **improves** (behaviour changed for quality). The moment a transform changes behaviour, it belongs in the forge, not here.

## Open questions for the plan

- The equivalence-judge prompt: exact comparison rubric (how it decides regressed vs diverged from two transcripts) and its structured output schema.
- Runner cost: the loop runs the runner twice per case per round (teacher captured once, candidate each round). Define a budget ceiling and whether the teacher baseline is cached across rounds (it should be - the original does not change).
- Whether the distill report persists as a sidecar corpus (like skill-forge's example report) or is returned inline.
- Standalone behaviour: distill mode needs subagents/forge; in pure chat (no subagents) the skill must degrade honestly - likely "local mode only; distill mode requires the runner harness" - confirm the degrade message.
