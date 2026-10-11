# North star

The goal every skill and report in `ai-native-toolkit` serves, in full. The root `CLAUDE.md` carries the short form and the feature test. Back to the [Map of Content](../index.md).

## Externalized context

Make an AI contributor feel like an engineer who has been in the org eighteen months, not a brand-new hire. The difference is *externalized context*, not capability. An AI is structurally always the new hire - each session starts with an empty head and one narrow context window - so the codebase has to supply the tenure: a navigable map, load-bearing contracts made explicit, complexity made *locally* legible so the relevant slice fits one keyhole.

## Legibility you can trust

The safety half is non-negotiable: the goal is **legibility you can trust, not omniscience you can't.** An agent fluent about code nobody can verify is the dangerous failure, not the win. Answers must stay anchored to code a human can spot-check.

## Guardrails protect the contributor

The other half is the same ethic pointed at the write side. When a contributor makes a mistake, ask "what made it possible, and what would make it impossible next time?", not "who's to blame". Guardrails (linters, architecture tests, CI gates, coverage, review automation - Layers 3-7) are not a leash on the AI; they protect the contributor from costly mistakes by design, the way an org protects a human engineer with RBAC and staged environments rather than hope. This is **correctness by construction** / poka-yoke: make the wrong action hard and the right action the path of least resistance.

## The tendencies the guardrails trace to

The write-side mistakes worth guarding against are not hypothetical. They are the known tendencies of an AI contributor, observed across models and worth naming so every guardrail traces to one:

- **Accretion.** An agent does what is asked, and what is asked is feature after feature. Nothing in that loop ever asks for a refactor, so files only grow. Absent a consciously requested restructuring, size and complexity ratchet monotonically upward.
- **Unactioned intent.** An agent records promises it never returns to keep: TODO / FIXME / "deprecated, use X" / "remove after migration" comments. These are *promissory markers* - self-descriptions of the code's future with no pressure to come true. Aged and ignored, they are a lying map of intent, exactly as a stale doc is a lying map of behaviour.
- **Guardrail erosion.** Under pressure to make red go green, an agent loosens the check instead of fixing the root - a suppression here, a skipped test there, a widened threshold - hollowing out the very layers meant to protect it while the scaffolding still reads as Present.

All three are the same defect: a self-description (the file's shape, the comment's promise, the gate's verdict) under no pressure to stay true. The toolkit's job is to convert each tendency into a deterministic signal (the marker aged by the edits it survived, the file that only ever grows, the suppression count that climbs) and a ratchet that makes the honest action the cheap one.

## The feature test

Judge any change to a skill or report by: does it help a fresh agent (the map), keep its answers verifiable (trust), *and* make the wrong action hard to take (guardrails - protecting the contributor, not policing it)? When adding a signal or rule, name the contributor tendency it compensates for: a guardrail that does not trace to a failure mode is decoration. Prefer honest-degrade over impressive-but-wrong, local comprehension over global, and "ground the claim in the file" over a fluent narrative.
