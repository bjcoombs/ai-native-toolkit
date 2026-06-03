# Design: instruction optimizer - cognitive-ergonomics family, directive-clarity first

Date: 2026-06-03
Status: Follow-up stage - depends on the semantic-compress v2 distillation harness
Predecessor: `2026-06-03-semantic-compress-distillation-design.md`

## Why this is a separate, later spec

The v2 distillation spec builds the **first** behaviour-preserving transform (compress) and the **A/B harness** that validates it. This spec is the **second stage**: it adds a second transform (directive-clarity) and names the umbrella it belongs to. It is deliberately split off so the harness ships and proves itself before the family expands - each transform must earn its place by A/B, not by being designed in up front.

## The frame: instruction cognitive-ergonomics

An LLM is trained on human text and inherits human processing biases, so how an instruction is *framed* changes how much work the model does to act on it - independent of what the instruction says. This is a useful **metaphor** (the model has no wellbeing) drawn from cognitive science and CBT: it is a *source of hypotheses* about instruction quality, never a source of truths. Candidate qualities it generates:

- **Directive clarity** - the instruction names the action to take (vs a negation, a bare fact, a vague pointer, or an ordering rule the model must convert into an action).
- **Cognitive load** - the instruction fits the model's working context (vs sprawling, requiring the model to hold too much at once).
- **Goal clarity** - a concrete next action (behavioural activation) vs vague avoidance.
- **Consistency** - no contradictory rules the model must reconcile (dissonance).

Each is a **hypothesis, A/B-validated before it ships**, exactly because anthropomorphising would otherwise let us assert benefits that are not real. The frame's value is generating candidates; the A/B harness is what earns them in. This spec ships **only directive-clarity**; the others are named as future candidates, not built.

## The evidence: the directive-clarity A/B preview

Before this spec, a manual A/B tested the hypothesis "positive framing beats negative framing for LLM instructions." Two versions of the same PR-handling rule set - identical rules and specificity, only the voice differing (negative gotchas vs positive directions) - were run on the same task; each runner reported where it had to *unpack* a rule into an action.

The result **refined** the hypothesis rather than confirming it:

- A positive *imperative with a concrete object and example* (`pipe gh to jq, filter with == "FAILURE"`) was acted on immediately.
- A positive *fact* ("CodeRabbit resolves its own threads") needed exactly as much unpacking as a negative prohibition - the model still had to derive "so I don't reply, I just push."
- An *ordering* rule ("get to green before refactoring" / "never refactor while red") needed unpacking in **both** polarities.
- The single worst rule was the **vague** one ("resolution is elsewhere") - it named where not to act without naming where to - regardless of polarity.

**Conclusion: the lever is directive clarity, not positive polarity.** Stating the concrete action bakes in the model's pre-thinking; a bare negation, a positive fact, a vague pointer, and an ordering rule all leave the model to derive the action. Positive phrasing helps mainly as a *proxy* for naming the action.

Caveats kept honest: this was n=1, a crafted pair, with self-reported unpacking. It is a directional first read, not proof - which is exactly why the shipped transform is gated by the real harness over many cases.

## Transform #2: directive-clarity

A behaviour-preserving optimizer transform (it belongs to `semantic-compress`'s optimizer family, gated by the v2 A/B harness):

1. **Detect** instructions that force the model to unpack an action: bare negations, facts-not-actions, vague pointers ("handle it appropriately", "elsewhere"), and ordering/policy rules stated without their concrete consequence.
2. **Rewrite** each into a concrete directive that names the action - **conservatively**: a negation that already implies one clear action ("never merge while X pending" -> "wait for X before merging") converts for free; a negation that encodes a *specific failure mode* (a battle-scar: "never give sonnet a second chance on the same task") may lose its specificity if positivised, so it is kept unless the rewrite provably preserves it.
3. **A/B-validate** with the harness: behaviour preserved (strict no-regression) **and** a measured efficiency gain (less unpacking / more direct action). A rewrite that preserves behaviour but shows no efficiency gain is not worth the change; a rewrite that regresses behaviour is reverted.

**Negation density is a smell, not a rule.** The transform never blanket-removes negatives. It flags instructions that make the model derive the action and proposes directive rewrites, each earning its place by the A/B. This respects that some negatives are the cheapest, most precise encoding of a hard-won lesson.

## Relationship to the rest of the toolkit

- **Composes the v2 harness.** This transform adds no new testing machinery; it uses the transform-agnostic A/B-equivalence capability (with the efficiency signal) that v2 adds to `skill-forge`.
- **Distinct from `skill-forge`.** Directive-clarity preserves output behaviour and only lowers cognitive cost - a refactor, not a quality change. If a rewrite changes what the agent does, that is a `skill-forge` concern, not this one.
- **A real first target exists.** The toolkit's own battle-scar skills (`marathon`, `pr-review-merge`) are negation-dense, while the user's global `CLAUDE.md` already preaches "express what TO do, not what to avoid" - so the toolkit is an immediate, honest test corpus for whether directive-clarity rewrites preserve the battle-scars' protective behaviour.

## Non-goals

- Not building the whole cognitive-ergonomics family - only directive-clarity. Low-load, goal-clarity, and consistency are named future candidates, each to be A/B-tested before it ships.
- Not blanket de-negation. Negatives that encode a specific failure mode and resist behaviour-preserving rewrite stay.
- Not a `skill-forge` lens. This is a fixing transform, not a flagging lens (a lens may follow if detection-without-fix proves useful).

## Open questions for the plan

- The efficiency metric: how the harness quantifies "less unpacking" objectively (runner reasoning length? a directness rubric the equivalence judge scores? error-rate on the task?) beyond the self-report used in the preview.
- Detection precision: how the transform distinguishes a convert-for-free negation from a battle-scar that must stay, without a human in the loop.
- Whether directive-clarity and compress run as independent passes or a single combined optimizer pass over a document.
