---
name: semantic-compress
description: "Make an LLM-directed document smaller while preserving what it does. Two modes: a local span-level core->pointer pass, and an A/B-validated distill loop that produces the smallest document that behaves the same as the original. Point at core knowledge the model already holds (a concept name activates it); keep project-specific detail explicit and verbatim. TRIGGER when asked to compress, tighten, shorten, or strip a prompt / instruction / system message meant for an LLM; to distill a skill; to compress a whole document; to make this smaller while preserving behaviour; to A/B test a compression or produce a behaviourally-equivalent compression; when an instruction set explains concepts the model already knows from training; or when reducing token cost of an LLM-directed prompt without losing meaning. Not for human-facing prose - that is /deslop."
---

# Semantic Compress

Make a document written **for an LLM reader** smaller while preserving what it *does*. The essence of an LLM-directed document is **behavioural**, not textual - the behaviour it induces in the reading model across the tasks it handles. Compression splits content into two kinds and treats each correctly:

- **Core knowledge** - anything in the model's training (named concepts, standard definitions, common-domain background). Replace the explanation with a **pointer**: the bare concept name or a short cue that activates the right knowledge. A pointer costs a few tokens and reliably switches on the model's existing understanding; a full explanation is wasted tokens, and *deleting the concept entirely* gambles that it is already active in the model's reasoning - often it is not, and disambiguation suffers.
- **Project / bespoke knowledge** - anything the model cannot know from training: specific facts, local decisions, constraints, and non-standard twists on a known concept. Keep these **explicit and verbatim**. The model has no other source for them.

Compression is therefore **point at core, spell out bespoke**. Pointing is not deletion and it is not full explanation; it is the minimum that both activates the right core knowledge and preserves every bespoke detail.

## Mode Selection

This skill operates in one of two modes, selected **deterministically**:

| Input | Mode | What happens |
|-------|------|-------------|
| Short snippet with an obvious local swap, no behavioural surface | **Local** | Quick core->pointer pass, no A/B |
| Whole document / skill / system prompt | **Distill** | Full A/B-validated loop |

**Default to distill** when:
- The input is a skill, system prompt, or instruction document
- Behaviour preservation matters
- The user asks for "smaller but same behaviour"

**Local is permitted only** when **all** hold:
- The input is a short span (< 500 chars)
- There is an obvious single core->pointer swap
- No downstream behaviour depends on it

When in doubt, distill: a local edit cannot, by construction, preserve a global behavioural property, so anything with a behavioural surface goes through the A/B gate.

## Hard Rule: Behavioural Evidence Required

A compression is **never accepted on inspection** - only on behavioural evidence from an A/B run.

This skill must refuse to output a compressed document that has not passed an A/B equivalence run against the original. Introspection about behaviour ("this should work the same") is structurally unreliable - the model guesses optimistically. Execution over the transfer set is the only arbiter.

This rule binds **distill mode** (the rule's home: whole-document compression always carries behavioural risk). Local mode is the deliberate, narrow exception - a span small enough (< 500 chars, single obvious swap, no downstream behaviour) that the behavioural risk is negligible by construction. The moment a local edit touches a behavioural surface, it is no longer local: it is a distill, and the gate applies.

## Local Mode

The v1 span-level operation: find a span that explains a concept the model already holds, replace it with a pointer, keep every bespoke detail verbatim. These steps are also the **inner micro-operation** distill mode regenerates with (`references/distill-loop.md`, Part 2, step 1).

### Step 0: Audience gate

This skill applies only when the **LLM is the audience** for the explanation. If the text explains a concept *to a human* (onboarding notes, a message to teammates, docs for new hires), the explanation is not redundant for its real audience - leave it. Compress only the spans the model itself is meant to read and act on.

**Nested / wrapped instructions.** If the input wraps an instruction the model is meant to process (e.g. "preprocess this instruction before executing it: '...'", or a quoted prompt to compress), the wrapper is a **meta-directive to you** - act on it, do not emit it. Compress the *quoted payload* by the rules below and return only that. The payload's audience is the model, so the audience gate is satisfied for the payload regardless of the wrapper.

### Step 1: Split each span into core vs bespoke

Read the input. For each span, classify:

- **Core** - a named concept or standard definition the model already holds (Chesterton's Fence, the Agile Manifesto, idempotency, SOLID, optimistic locking in its standard form).
- **Bespoke** - information with no training source: a specific number, a local choice, a constraint, an exception, or a **non-standard redefinition** of an otherwise-known term.

A single sentence often contains both. Split at that seam.

### Step 2: Core -> pointer

Replace a core-knowledge explanation with the smallest cue that activates it - usually the concept's name, optionally one disambiguating word:

- "the principle that you shouldn't remove something until you understand why it's there" -> "Chesterton's Fence"
- "observe-orient-decide-act faster than the competitor" -> "OODA"

**Always emit the pointer**, with one exception: if a surviving bespoke span already names the concept literally, the pointer is redundant - omit it then, and only then. Never drop the pointer on the grounds that the frame is merely "implied" - that judgement is self-certifying and is exactly the escape that collapses this skill back into deleting what the model knows. When in doubt, keep the pointer; it costs almost nothing.

### Step 3: Bespoke -> explicit, verbatim

Keep every bespoke span unchanged: specific facts, decisions, constraints, and especially **non-standard twists**. If a known term is redefined locally ("optimistic locking - but we hash the whole record, not a version counter"), the twist is bespoke: keep it in full. Dropping it because the term looks familiar is the most dangerous failure this skill can make.

### Step 4: Self-sufficiency check

Could the model act correctly on the output alone? If a pointer is too thin to disambiguate, widen it by one cue word - never back to a full explanation. If a bespoke detail was lost, restore it verbatim. The all-core degenerate case (input that is purely known concepts with nothing bespoke and no instruction) compresses to its pointers; if there is also no instruction to act on, the output is just those pointers - not empty text and not a meta-note.

### Step 5: Output

Clean text: pointers for core, explicit bespoke. No meta-commentary, no "[compressed]" markers, no "see X" links - the pointer *is* the reference, inline and natural.

### Examples

#### Core -> pointer, bespoke kept
**Input:** "When evaluating whether to remove a legacy feature, first understand why it was created - you shouldn't destroy something without understanding its purpose, as it may serve a need you're unaware of. Our legacy auth module was built for EU data-residency compliance that still applies to German users."
**Output:** "Chesterton's Fence on removing the legacy auth module: built for EU data-residency compliance, still applies to German users."

#### All-core -> pointers (degenerate case)
**Input:** "Remember to follow the Agile Manifesto and the SOLID principles in this project."
**Output:** "Follow Agile and SOLID."
Nothing bespoke and no instruction beyond the frame, so it compresses to the bare pointers - not to empty text.

#### Non-standard twist is bespoke
**Input:** "We use optimistic locking - by which we specifically mean the client sends a hash of the entire record snapshot, and the server rejects on any field mismatch, not just a version-counter bump."
**Output:** "Optimistic locking, but our variant: client sends a hash of the whole record snapshot; server rejects on any field mismatch, not a version-counter bump."

#### Nothing core
**Input:** "The staging cluster runs 3 nodes in eu-west-2 with a 4GB heap cap per node; the nightly backup starts at 02:00 UTC to the cold-storage bucket."
**Output:** (unchanged - all bespoke)

#### Human audience -> do not compress
**Input:** "Reminder for new hires: 'idempotent' means you can safely retry an operation. Skim the API onboarding doc before Friday."
**Output:** (unchanged - the definition is for new hires, not the model)

### Anti-patterns

- **Deleting the concept instead of pointing at it.** Pure elision gambles the concept is already active; a pointer guarantees activation at near-zero cost. "The frame is implied" is not a licence to delete - emit the pointer unless a bespoke span literally names the concept.
- **Dropping the bespoke part.** "Chesterton's Fence on the legacy module" without the EU-compliance reason is useless - the reason is the whole payload.
- **Dropping a non-standard twist as if it were the standard concept.** If a known term is locally redefined, the redefinition is bespoke. Keep it.
- **Re-explaining core knowledge.** A pointer suffices; a paragraph is waste.
- **Compressing human-directed text.** The audience gate comes first.
- **Meta-commentary.** No "[pointer]", no "// core concept". The output reads as natural, shorter text.

## Distill Mode

The headline operation: produce the **smallest document that behaves the same as the original**, with acceptance gated on A/B behavioural evidence (the Hard Rule above), never on inspection. Distill mode composes two reference docs:

- `references/transfer-set-design.md` - derives and confirms the transfer set (the operational definition of the document's essence).
- `references/distill-loop.md` - the engine: teacher baseline capture, candidate regeneration, and the iterate-to-minimal controller.

<!-- chat-replace:distill-availability -->
The loop **composes `skill-forge`'s A/B equivalence capability** (`skills/skill-forge/references/ab-equivalence.md`) for the behavioural test - exactly as `marathon` composes `pr-review-merge`. This skill owns compression (transfer set, candidate regeneration, the loop, the report); `skill-forge` owns the behavioural comparison (running the runner over the transfer set, judging equivalence per case).

### The distill loop (seven steps)

1. **Define the transfer set.** Derive behavioural test cases from the document across the four taxonomy types (happy / edge / adversarial / composition). The user confirms or augments them before any baseline run - the transfer set *is* the operational definition of essence, so the user signs off. The skill flags thin coverage. See `references/transfer-set-design.md`.
2. **Capture the teacher.** Run the **original** document over each case and record the behaviour it induces (the disciplines enforced, the outputs produced). This baseline is the equivalence target, captured **once** and cached across rounds.
3. **Compress = regenerate, not edit.** Produce a smaller candidate against the behavioural spec: the local core->pointer move (Local Mode above), de-duplication, and dropping prose the baseline proves was never acted on. Regeneration - rewriting to function - is what lets it get genuinely smaller; trimming alone cannot.
4. **A/B validate.** Run the **candidate** over the same transfer set; the equivalence judge compares candidate-vs-teacher behaviour per case under **strict no-regression**. This is the only acceptance gate.
5. **On any regression, the divergence names what was load-bearing.** The judge's behaviour delta names the lost behaviour; add back the minimum that restores it, then re-validate.
6. **Converge** on the smallest candidate that still passes. Stop at diminishing returns (a round that can only shrink further by regressing) or a budget ceiling (default 5 rounds).
7. **Output** the minimal equivalent document plus an **A/B distillation report** (`references/distillation-report-template.md`).

**Strict no-regression gate.** The candidate passes iff, on every transfer-set case, it induces every behaviour the original induced. Any dropped behaviour is a fail (essence lost) and triggers add-back. Incidental improvements are acceptable but never required and never the goal - the goal is faithful, smaller reproduction. The gate is behavioural equivalence, not textual similarity.

### Distill mode example
**Input:** "Distill this CLAUDE.md to the smallest version that behaves the same"
**Output:** The minimal equivalent document plus an A/B distillation report (`<document-name>-distillation-report.md`) recording the size delta, the transfer set and its coverage, per-case equivalence verdicts, what was dropped vs load-bearing, and the distribution-shift caveat.

## Distribution-Shift Guard

A distillation is only valid over the transfer set it was tested against - the same overfitting risk model distillation faces outside its transfer distribution. The transfer set is the operational definition of essence *for this run*; behaviour outside its coverage is unproven.

### Mitigations

1. **Push for diverse coverage.** The transfer set should span all four case types (happy / edge / adversarial / composition). A narrow transfer set (all happy-path) permits aggressive compression that breaks on edge or adversarial inputs - the breadth of the set bounds the safety of the compression.
2. **Stay conservative.** Keep anything not **proven** behaviourally inert by the A/B. If a section was never exercised by any case, it is **not proven inert** - it might be load-bearing for untested inputs. The conservative default is to keep it.
3. **Name the coverage.** The report explicitly states which sections were exercised and which were not. Silence about untested behaviour is forbidden.

### What the guard prevents

- Compressing away a section that only matters for adversarial inputs when the transfer set was all happy-path.
- Claiming "same behaviour" when behaviour outside the transfer set was never checked.
- The user believing the compression is universally valid when it is only valid for the tested cases.

### Coverage threshold

If **fewer than 70%** of the identified behaviour-inducing sections are covered by at least one case, **warn the user before proceeding** and offer to auto-generate additional cases for the uncovered sections. A distillation gated on a thin set makes a weak equivalence claim - say so loudly rather than proceed silently.

### Conservative-default logic

```
For each section in the original:
  if exercised_by_at_least_one_case(section):
    if A/B says inert:        can drop
    if A/B says load-bearing: must keep (pointed or de-duped, never dropped)
  else:
    section is NOT PROVEN INERT
    keep by default (conservative)
    report as "kept (uncovered, conservative default)"
```

### Report integration

The A/B distillation report (`references/distillation-report-template.md`) makes the coverage legible:

- Transfer-set coverage percentage in the header.
- Thin-coverage warnings listed in the Transfer Set section.
- Uncovered-but-kept sections labelled "conservative default" under What Proved Load-Bearing.
- The distribution-shift caveat stated verbatim: behaviour outside the transfer set is not guaranteed equivalent.

## Provenance

This skill was hardened with `/skill-forge` before shipping - the forge changed its central thesis from delete-what-the-model-knows to point-at-core / spell-out-bespoke. See [forge-report](references/forge-report.md). Distill mode (v2) extends it from a local textual edit to a holistic, A/B-validated behavioural-equivalence loop. The two-mode v2 was re-forged, and distill mode was validated end-to-end on a real engineering `CLAUDE.md` (full A/B, 30.6% reduction at zero regressions); see [forge-report-v2](references/forge-report-v2.md) and [acceptance-distillation-report](references/acceptance-distillation-report.md).
