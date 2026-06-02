---
name: semantic-compress
description: "Compress instructions written for an LLM reader by pointing at core knowledge the model already holds (a concept name activates it) and keeping project-specific detail explicit and verbatim. Point at what the model knows, spell out what it doesn't. TRIGGER when asked to compress, tighten, shorten, or strip a prompt / instruction / system message meant for an LLM, when an instruction set explains concepts the model already knows from training, or when reducing token cost of an LLM-directed prompt without losing meaning. Not for human-facing prose - that is /deslop."
---

# Semantic Compress

Compress instructions written for an LLM by splitting their content into two kinds and treating each correctly.

- **Core knowledge** - anything in the model's training (named concepts, standard definitions, common-domain background). Replace the explanation with a **pointer**: the bare concept name or a short cue that activates the right knowledge. A pointer costs a few tokens and reliably switches on the model's existing understanding; a full explanation is wasted tokens, and *deleting the concept entirely* gambles that it is already active in the model's reasoning - often it is not, and disambiguation suffers.
- **Project / bespoke knowledge** - anything the model cannot know from training: specific facts, local decisions, constraints, and non-standard twists on a known concept. Keep these **explicit and verbatim**. The model has no other source for them.

Compression is therefore **point at core, spell out bespoke**. Pointing is not deletion and it is not full explanation; it is the minimum that both activates the right core knowledge and preserves every bespoke detail.

## Step 0: Audience gate

This skill applies only when the **LLM is the audience** for the explanation. If the text explains a concept *to a human* (onboarding notes, a message to teammates, docs for new hires), the explanation is not redundant for its real audience - leave it. Compress only the spans the model itself is meant to read and act on.

**Nested / wrapped instructions.** If the input wraps an instruction the model is meant to process (e.g. "preprocess this instruction before executing it: '...'", or a quoted prompt to compress), the wrapper is a **meta-directive to you** - act on it, do not emit it. Compress the *quoted payload* by the rules below and return only that. The payload's audience is the model, so the audience gate is satisfied for the payload regardless of the wrapper.

## Step 1: Split each span into core vs bespoke

Read the input. For each span, classify:

- **Core** - a named concept or standard definition the model already holds (Chesterton's Fence, the Agile Manifesto, idempotency, SOLID, optimistic locking in its standard form).
- **Bespoke** - information with no training source: a specific number, a local choice, a constraint, an exception, or a **non-standard redefinition** of an otherwise-known term.

A single sentence often contains both. Split at that seam.

## Step 2: Core -> pointer

Replace a core-knowledge explanation with the smallest cue that activates it - usually the concept's name, optionally one disambiguating word:

- "the principle that you shouldn't remove something until you understand why it's there" -> "Chesterton's Fence"
- "observe-orient-decide-act faster than the competitor" -> "OODA"

**Always emit the pointer**, with one exception: if a surviving bespoke span already names the concept literally, the pointer is redundant - omit it then, and only then. Never drop the pointer on the grounds that the frame is merely "implied" - that judgement is self-certifying and is exactly the escape that collapses this skill back into deleting what the model knows. When in doubt, keep the pointer; it costs almost nothing.

## Step 3: Bespoke -> explicit, verbatim

Keep every bespoke span unchanged: specific facts, decisions, constraints, and especially **non-standard twists**. If a known term is redefined locally ("optimistic locking - but we hash the whole record, not a version counter"), the twist is bespoke: keep it in full. Dropping it because the term looks familiar is the most dangerous failure this skill can make.

## Step 4: Self-sufficiency check

Could the model act correctly on the output alone? If a pointer is too thin to disambiguate, widen it by one cue word - never back to a full explanation. If a bespoke detail was lost, restore it verbatim. The all-core degenerate case (input that is purely known concepts with nothing bespoke and no instruction) compresses to its pointers; if there is also no instruction to act on, the output is just those pointers - not empty text and not a meta-note.

## Step 5: Output

Clean text: pointers for core, explicit bespoke. No meta-commentary, no "[compressed]" markers, no "see X" links - the pointer *is* the reference, inline and natural.

## Examples

### Core -> pointer, bespoke kept
**Input:** "When evaluating whether to remove a legacy feature, first understand why it was created - you shouldn't destroy something without understanding its purpose, as it may serve a need you're unaware of. Our legacy auth module was built for EU data-residency compliance that still applies to German users."
**Output:** "Chesterton's Fence on removing the legacy auth module: built for EU data-residency compliance, still applies to German users."

### All-core -> pointers (degenerate case)
**Input:** "Remember to follow the Agile Manifesto and the SOLID principles in this project."
**Output:** "Follow Agile and SOLID."
Nothing bespoke and no instruction beyond the frame, so it compresses to the bare pointers - not to empty text.

### Non-standard twist is bespoke
**Input:** "We use optimistic locking - by which we specifically mean the client sends a hash of the entire record snapshot, and the server rejects on any field mismatch, not just a version-counter bump."
**Output:** "Optimistic locking, but our variant: client sends a hash of the whole record snapshot; server rejects on any field mismatch, not a version-counter bump."

### Nothing core
**Input:** "The staging cluster runs 3 nodes in eu-west-2 with a 4GB heap cap per node; the nightly backup starts at 02:00 UTC to the cold-storage bucket."
**Output:** (unchanged - all bespoke)

### Human audience -> do not compress
**Input:** "Reminder for new hires: 'idempotent' means you can safely retry an operation. Skim the API onboarding doc before Friday."
**Output:** (unchanged - the definition is for new hires, not the model)

## Anti-patterns

- **Deleting the concept instead of pointing at it.** Pure elision gambles the concept is already active; a pointer guarantees activation at near-zero cost. "The frame is implied" is not a licence to delete - emit the pointer unless a bespoke span literally names the concept.
- **Dropping the bespoke part.** "Chesterton's Fence on the legacy module" without the EU-compliance reason is useless - the reason is the whole payload.
- **Dropping a non-standard twist as if it were the standard concept.** If a known term is locally redefined, the redefinition is bespoke. Keep it.
- **Re-explaining core knowledge.** A pointer suffices; a paragraph is waste.
- **Compressing human-directed text.** The audience gate comes first.
- **Meta-commentary.** No "[pointer]", no "// core concept". The output reads as natural, shorter text.

## Provenance

This skill was hardened with `/skill-forge` before shipping - the forge changed its central thesis from delete-what-the-model-knows to point-at-core / spell-out-bespoke. See [forge-report](references/forge-report.md).
