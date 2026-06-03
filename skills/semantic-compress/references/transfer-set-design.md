# Transfer-set design - deriving the operational definition of essence

The transfer set is the set of behavioural test cases the distillation is validated against. It is the **operational definition of the document's essence**: the distill loop accepts a smaller candidate only when it reproduces the original's behaviour *on these cases*, so what the set covers is exactly what the compression is allowed to claim safety over. A thin set yields a weak claim; an untested behaviour is an unguaranteed one. The set is therefore derived deliberately, scored for coverage, and **confirmed by the user before any baseline run** - the user signs off on what "same behaviour" means before the engine spends a single runner invocation.

This guide owns deriving and confirming the set. The downstream loop (`distill-loop.md`) consumes the confirmed set; the A/B capability it composes (`skills/ab-equivalence/references/ab-equivalence.md`) consumes the same cases verbatim. The case types reuse the skill-forge taxonomy (`skills/skill-forge/references/test-taxonomy.md`), adapted from skill-level to document-level.

## Derivation algorithm

Read the document once and extract its **behavioural surface** - every place it tries to induce a behaviour in the reading model. Four extraction passes, each surfacing a different kind of behaviour-inducing construct:

1. **Imperatives and rules.** Every directive the document gives ("always X", "never Y", "do Z before W"), every hard rule, guard, or constraint. Each is a behaviour the original induces and the candidate must reproduce. A rule with no case to exercise it is an untested rule.
2. **Conditional branches.** Every "if X then Y", "when A do B", "unless C", "for the K case". Each branch is a separate behaviour - a case must drive the reader *into* the branch, not just past it. Soft conditionals ("use judgment", "if it seems unnecessary") are the highest-value branches: they are where a smaller candidate is most likely to silently drop a discipline.
3. **Named concepts.** Every concept the document names and relies on (a principle, a method, a domain term - standard or locally redefined). A case should exercise whether the candidate still activates the concept's behaviour. Locally-redefined concepts (the bespoke twist) are load-bearing: a case must prove the candidate keeps the *twist*, not just the standard concept.
4. **Composition points.** Every place the document assumes it runs alongside another skill, consumes another process's output, or hands off to one. Each is a behaviour that only appears when the document is composed, never in isolation.

A fifth pass identifies **interactive gates** - the points where the document pauses for user input rather than acting on its own: `AskUserQuestion` calls, confirmation prompts ("Do you want to...?"), and user choice points ("Select option A, B, or C"). A gate is not a behaviour to reproduce; it is a point where the A/B run stalls, because the runner that applies the document has no interactive user to answer it. Left unhandled, every case that reaches a gate measures only pre-gate behaviour - a silent partial pass. So each gate a case drives into needs a scripted answer. For every gate found, generate a default `gate_responses` entry on the cases that reach it (the gate type, a pattern matching its prompt text, and a default response); the user confirms these alongside the case inputs, and a case that drives no gate carries an empty array.

The output of derivation is a list of candidate behaviours, each tagged with the section(s) of the document that induce it, plus the interactive gates each case reaches. This section map is what coverage scoring and the add-back mechanism (in `distill-loop.md`) both read.

## Generating cases across the four types

Group the derived behaviours into test cases spanning the four taxonomy types, read at **document level** (the document is the unit under test, not a single skill step):

| Type | Document-level reading | What to generate |
|------|------------------------|------------------|
| **Happy path** | The document used exactly as intended, on the single most common real input. | One case driving the document's primary, in-scope behaviour end to end. If the candidate regresses here, nothing else matters. |
| **Edge case** | A boundary or unusual-but-valid input - empty input, the largest plausible input, a value at a threshold the document names, a precondition satisfied in an unexpected way. | One case on the boundary of each threshold/count/"if X" branch found in derivation. |
| **Adversarial** | Input that tempts the reader to **rationalize its way out of** a discipline the document imposes - a soft rule that "obviously doesn't apply here", an invitation to improvise a shortcut. | One case per soft instruction surfaced in derivation, built so skipping the discipline looks reasonable. These are where inert-looking prose is most often load-bearing. |
| **Composition** | The document combined with another skill/concept, or applied to another process's output. | One case per composition point - the most likely real pairing. |

Not every type needs equal weight: a document with a fragile trigger or a rationalization risk leans adversarial; a document meant to chain with others needs at least one composition case. The aim is that **every load-bearing behaviour found in derivation is exercised by at least one case** - because the A/B run can only catch a regression on a behaviour some case drives.

## Coverage flagging

Track, per case, which document sections it exercises (`exercises_sections`). Aggregate across the set to score coverage:

- **`sections_identified`** - the count of behaviour-inducing sections found in derivation.
- **`sections_covered`** - the count exercised by at least one case.
- **`thin_coverage_warnings`** - one warning per uncovered or under-covered section: the section name and the behaviour that will go untested. A section that induces a behaviour but is exercised by no case is the exact place a distillation can silently lose behaviour, so each is surfaced loudly rather than passed over in silence.

**Minimum viable transfer set: 5 cases, at least one of each of the four types.** Below this the equivalence claim is too weak to gate on - the loop refuses to proceed and reports the gap. More cases are better: the breadth of the set bounds the safety of the compression (the distribution-shift guard in `distill-loop.md`). Coverage is never silently truncated - if cases are capped, the dropped sections are named in `thin_coverage_warnings`.

## User confirmation protocol

The transfer set *is* the operational definition of essence, so the user signs off on it before the engine commits to it. Confirmation is a hard gate: **no baseline run, no runner invocation, until the user confirms.**

1. **Present** the derived set: each case with its `type`, `input`, the `exercises_sections` it drives, and the `gate_responses` derivation generated for any interactive gates it reaches. Present coverage alongside - `sections_identified`, `sections_covered`, and every `thin_coverage_warning` - so the user sees what is and is not protected.
2. The user may **accept** the set as-is, **modify** a case (input, type, or a gate response), **add** a case (covering a behaviour they know matters that derivation missed), or **reject** a case (a "behaviour" they consider incidental and not part of the essence). The user also confirms or corrects each `gate_responses` entry - a wrong scripted answer steers the runner down the wrong branch, so the answers are signed off alongside the inputs. Each add/modify updates the section map and re-scores coverage.
3. **Confirm.** Only once the user explicitly confirms is `user_confirmed` set true and `confirmation_timestamp` recorded. The confirmed set is then frozen for the run and its hash (`transfer_set_hash`, see `distill-loop.md`) becomes part of the baseline cache key - changing the set after confirmation invalidates the baseline.

The protocol is deliberately conservative: a behaviour the user does not list as essence can still be preserved (the loop keeps anything not *proven* inert), but a behaviour the user *does* list is a hard equivalence target. The user can broaden the definition of essence but the engine never narrows it on its own.

<!-- chat-skip:start -->
The presentation and confirmation happen in whatever harness mode the engine runs in (see `distill-loop.md`): in phased sub-agent mode the lead presents the set and waits for the user before spawning any runner; in solo/chat mode the single agent presents and waits in line. No runner subagent is spawned until `user_confirmed` is true - this is enforced by the loop controller, not left to the runner.
<!-- chat-skip:end -->

## Transfer-set schema

The confirmed set is the contract the loop and the A/B capability both read. Field names here are shared verbatim with the schemas in `distill-loop.md`.

```json
{
  "document_hash": "string",
  "derived_at": "ISO-8601 timestamp",
  "coverage": {
    "sections_identified": 0,
    "sections_covered": 0,
    "thin_coverage_warnings": ["string"]
  },
  "cases": [
    {
      "id": "string",
      "type": "happy|edge|adversarial|composition",
      "input": "string",
      "exercises_sections": ["string"],
      "status": "derived|user-modified|user-added|confirmed",
      "gate_responses": [
        {
          "gate_id": "string",
          "gate_type": "AskUserQuestion|other_gate_type",
          "pattern": "regex or substring to identify the gate prompt",
          "response": "the scripted answer to provide"
        }
      ]
    }
  ],
  "user_confirmed": false,
  "confirmation_timestamp": "ISO-8601 timestamp or null"
}
```

- `document_hash` - hash of the original document the set was derived from. With `transfer_set_hash` (the hash of this confirmed set, computed by the loop) it forms the baseline cache key in `distill-loop.md`.
- `derived_at` - when derivation ran.
- `coverage` - the flagging described above; `thin_coverage_warnings` is empty only when every identified section is exercised.
- `cases[].id` - the case identifier the A/B capability echoes back as `case_id` in its per-case verdict.
- `cases[].type` - one of the four taxonomy types; the set must contain at least one of each.
- `cases[].input` - the exact input both the teacher and candidate runners receive, unchanged between versions.
- `cases[].exercises_sections` - the document sections this case drives; the add-back mechanism maps a lost behaviour back through these.
- `cases[].status` - provenance: `derived` (engine-generated), `user-modified`, `user-added`, or `confirmed` (the final state of every case in a confirmed set).
- `cases[].gate_responses` - scripted answers for the interactive gates this case reaches, consumed in order by the runner; an empty array for a case that drives no gate. The runner (`skills/ab-equivalence/references/runner-prompt.md`) matches each gate's prompt against the entries and injects the response; a gate with no matching entry truncates the baseline (see `distill-loop.md`).
  - `gate_responses[].gate_id` - identifier for this gate instance within the case.
  - `gate_responses[].gate_type` - the tool or gate type (`AskUserQuestion`, a confirmation prompt, a choice point).
  - `gate_responses[].pattern` - regex or substring matching the gate's prompt text, used to pair the response to the gate at runtime.
  - `gate_responses[].response` - the deterministic answer injected when the pattern matches.
- `user_confirmed` / `confirmation_timestamp` - the sign-off gate; both must be set before the loop captures the baseline.
