# Cognitive ergonomics - the optimizer-family frame

What this doc does: state the frame that generates the optimizer transforms (directive-clarity ships here; others are candidates only), and bound it with the caveat that keeps the frame honest. This is the *why* behind the family; the *how* lives in the per-transform docs.

## The anthropomorphism caveat (read this first)

This entire frame rests on a **metaphor**, and the metaphor is a source of *hypotheses, never of truths*.

The frame says: an LLM is trained on human text and so inherits human processing tendencies, therefore how an instruction is *framed* changes how much work the model does to act on it. That is a useful generator of ideas. It is not evidence of anything. **The model has no wellbeing, no effort budget, no felt difficulty.** Anthropomorphising it would let us assert that a "clearer" or "lighter" instruction *is* better - and assertion is exactly the failure mode this family exists to avoid.

So the rule is absolute: **every quality the frame proposes is a hypothesis to be validated behaviourally, never asserted.** The validator is the A/B equivalence harness's recorded efficiency signal (`skills/ab-equivalence/references/ab-equivalence.md`) - a measured directness delta across a transfer set, not a human's intuition that the rewrite "reads better". A transform earns its place only when the harness measures the effect it claims. If the measurement is absent, the claim is anthropomorphic decoration and does not ship.

## The frame

Treat instruction text as having an **ergonomic cost** distinct from its **content**. Two documents can say the same thing and induce the same behaviour, yet one forces the reading model through more interpretive work to get there - unpacking a prohibition into an action, inferring an action from a stated fact, resolving a vague pointer. The frame's claim is narrow and testable: **reducing that interpretive work, while holding content constant, is a real and measurable improvement** - measurable as a directness gain at zero behavioural regression.

"While holding content constant" is the load-bearing constraint. An ergonomic transform is *behaviour-preserving by construction* or it is not an ergonomic transform - it is an edit that changes what the document does, which is a different and riskier operation. The whole family is defined by the pairing: **lighter to act on, and proven to behave the same.**

## Candidate qualities

The frame generates a family of candidate transforms. Each names a distinct kind of interpretive work to reduce. **Only directive-clarity is built here.** The rest are named to map the space, explicitly not to implement.

- **directive-clarity (shipped here).** Reduce the work of deriving *what action to take*. Rewrites latent-action instructions (bare negations, facts-not-actions, vague pointers, ordering rules) into directives that name the action. Validated by a measured directness gain at no regression.
- **cognitive-load (candidate, not built).** Reduce the work of *holding the relevant context*. Hypothesis: a document that keeps the slice needed for one decision local - rather than scattered across sections the reader must assemble - is lighter to act on. Unvalidated; do not build.
- **resolution-order (candidate, not built).** Reduce the work of *reconciling conflicting instructions*. Hypothesis: when two rules can both fire, stating their precedence explicitly removes an arbitration step. Unvalidated; do not build.

These candidates are deliberately left as hypotheses. Naming them maps the territory without committing to it; building them before the harness can measure their claimed effect would be asserting a benefit the frame only hypothesised. The discipline is the point.

## How this family differs from skill-forge

skill-forge and the optimizer family answer **different questions about different inputs**:

- **skill-forge judges *quality*.** Its five-lens panel scores whether a skill is *good* in absolute terms - is it clear, complete, correct, robust? It runs after authoring as a promotion gate. The question is "is this skill ready?"
- **The optimizer family makes documents *lighter to act on while proving behaviour is preserved*.** It does not judge whether the document is good; it takes a document whose behaviour is already the target and produces a smaller or clearer version that behaves the same. The question is "does this transformed version still do exactly what the original did, with less interpretive cost?"

The two compose without overlap: forge decides a document is worth keeping; the optimizer family makes a kept document cheaper to act on. They share infrastructure - the optimizer family's behavioural validator is ab-equivalence - but the judgements are orthogonal. Absolute quality is one axis; behaviour-preserving transformation is another.

## The honest-degrade commitment

The frame's value is bounded by its caveat, and that boundary is a feature. A transform in this family that cannot show a measured effect does not get to claim one - it either fails its gate or never ships. Better an honest "we could not measure a gain here, so we kept the original" than an impressive rewrite justified by an intuition about a model that has no inner life to appeal to. The frame generates the hypotheses; the harness decides which are real.
