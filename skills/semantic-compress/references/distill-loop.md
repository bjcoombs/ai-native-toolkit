# The distill loop - iterate to the minimal behaviourally-equivalent document

This is the engine of distill mode: the loop that produces the **smallest document that behaves the same as the original**, with acceptance gated on A/B behavioural evidence, never on inspection. It composes skill-forge's A/B equivalence capability (`skills/skill-forge/references/ab-equivalence.md`) for the behavioural test, exactly as `marathon` composes `pr-review-merge` - this engine owns compression (deriving candidates, the loop, the report); skill-forge owns the behavioural comparison (running the runner over the transfer set, judging equivalence).

**The hard rule, stated once and binding on everything below: a compressed document is never accepted on inspection - only on A/B behavioural evidence from a run against the original.** Introspection about whether a smaller version "still does the same thing" is structurally unreliable - the model guesses optimistically. Execution over the transfer set is the only arbiter. The engine must refuse to output a candidate that has not passed an A/B equivalence run.

The loop runs on a **confirmed** transfer set (`transfer-set-design.md`) - the operational definition of essence the user has signed off on. It has three parts: capture the teacher baseline once, regenerate a smaller candidate, and run the controller that iterates the two against the A/B gate until it converges on the minimal passing candidate.

## Part 1 - Teacher baseline capture

For each confirmed case, capture how the **original** document behaves. This is the equivalence target every candidate is measured against.

<!-- chat-skip:start -->
For each case, spawn a runner using skill-forge's exact `runner-prompt.md` template, unchanged - drop in the **original** document as the skill draft and the case `input` as the test-case input. The runner applies the document verbatim and returns its five-field self-report. The runner and runner-prompt are reused exactly as the forge loop uses them; nothing about how a single version is executed changes.
<!-- chat-skip:end -->

Record the runner's full self-report per case - all five required fields - and extract the disciplines the document held the runner to:

- **`output_produced`** - the exact output the document instructed the runner to produce.
- **`steps_followed`** / **`steps_skipped`** - each step the runner followed or skipped, with the reason for any skip.
- **`ambiguities_hit`** - each ambiguity in the document and how the runner proceeded despite it.
- **`improvisations`** - anything the runner did that the document did not explicitly instruct, and why.
- **`wanted_to_deviate`** - any point where following the document felt wrong but the runner did it anyway.
- **`disciplines_enforced`** - extracted from the self-report (read from `steps_followed`, `improvisations`, and `wanted_to_deviate`): the rules, guards, and disciplines the original actually held the runner to. This is the load-bearing list - a candidate that lets any of these slide has regressed.

A behaviour counts as part of the baseline **only if it actually appears in the teacher transcript** - the engine compares observed behaviour to observed behaviour, never a candidate against what the original document *says* it should do. A faithful distillation preserves the original's gaps as well as its disciplines.

### Caching rule (the teacher is captured once)

The original does not change across the loop, so its baseline is **captured once per run and reused across every candidate round**. This is a hard rule, not an optimization: re-running the teacher each round wastes runner budget and risks teacher-side noise the judge would mistake for a candidate change.

- **Cache key: `(document_hash, transfer_set_hash)`** - the hash of the original document and the hash of the confirmed transfer set.
- **Invalidate** the cache if either changes: a different original document, or any post-confirmation edit to the transfer set (add/modify/reject a case). A changed set means `user_confirmed` must be re-established (see `transfer-set-design.md`) and the baseline recaptured.
- **Budget: exactly one runner invocation per case, a fixed cost** paid once regardless of how many candidate rounds follow.

### Baseline schema

```json
{
  "document_hash": "string",
  "transfer_set_hash": "string",
  "captured_at": "ISO-8601 timestamp",
  "cases": [
    {
      "case_id": "string",
      "self_report": {
        "output_produced": "string",
        "steps_followed": ["string"],
        "steps_skipped": ["string"],
        "ambiguities_hit": ["string"],
        "improvisations": ["string"],
        "wanted_to_deviate": ["string"]
      },
      "disciplines_enforced": ["string"]
    }
  ]
}
```

- `document_hash` / `transfer_set_hash` - the cache key; both must match the current run or the baseline is invalid and is recaptured.
- `cases[].case_id` - matches the transfer-set `cases[].id` and the A/B capability's `case_id`.
- `cases[].self_report` - the runner's five required fields verbatim, field names shared with `runner-prompt.md`.
- `cases[].disciplines_enforced` - the disciplines extracted from the self-report; the equivalence target for that case.

## Part 2 - Candidate regeneration

**Regenerate, do not trim.** Producing a smaller candidate is rewriting the document to function on the behavioural spec - not deleting spans from the original. Trimming can only ever remove text locally; regeneration is what lets a document get genuinely smaller while still inducing the same behaviour. The candidate is written against the baseline (what the document *does*), not against the original's prose.

Three micro-operations, applied **in this fixed order**, each preserving the load-bearing identifications the prior step made:

1. **Core -> pointer** (the v1 inner micro-op). For each span explaining a concept the model already holds from training, replace the explanation with the smallest pointer that activates it (the concept name, optionally one disambiguating cue). Keep every bespoke/project-specific detail verbatim, including non-standard twists on a known concept - the v1 rules in `../SKILL.md` govern this step exactly. Pointing is not deletion.
2. **De-duplicate repeated concepts.** Where the document states the same concept, rule, or instruction in more than one place, keep one authoritative statement and drop the repetitions - provided no repetition carries a bespoke detail the others lack. De-dup runs *after* core->pointer so it operates on already-pointed spans and never merges two spans that look alike but differ in a load-bearing twist.
3. **Drop behaviourally-inert prose.** Remove prose the **baseline proves was never acted on** - text that induced no behaviour, discipline, step, or output in any teacher transcript. This is the only license to delete, and it is evidence-gated: a span is inert only if the baseline shows no case's behaviour depended on it. Inert-prose removal runs last so it cannot delete a span that core->pointer turned into a load-bearing pointer or that de-dup chose as the authoritative statement.

**Two hard constraints across all three steps:**

- **Never delete a section that induced a baseline behaviour.** If any teacher transcript's behaviour, discipline, step, or output traces to a section, that section is load-bearing and is kept (pointed or de-duped, never dropped). The baseline is the record of what is load-bearing.
- **Keep every bespoke/project-specific detail verbatim.** Specific facts, local decisions, constraints, exceptions, and non-standard redefinitions have no training source - the model cannot recover them from a pointer. They survive every step unchanged.

Each step preserves the prior step's load-bearing identifications: core->pointer marks which spans became activating pointers, de-dup must not drop a span another step marked authoritative, and inert-prose removal must not touch any span the earlier steps or the baseline marked load-bearing.

### Size-tracking schema

Track size before and after regeneration so the report can state the delta. Token estimate is `characters / 4`.

```json
{
  "original_size": {
    "characters": 0,
    "tokens_estimate": 0,
    "lines": 0
  },
  "candidate_size": {
    "characters": 0,
    "tokens_estimate": 0,
    "lines": 0
  },
  "delta": {
    "characters_removed": 0,
    "compression_ratio": 0.0
  }
}
```

- `*_size.characters` / `lines` - raw counts of the original and the current candidate.
- `*_size.tokens_estimate` - `characters / 4`, a coarse token proxy (no tokenizer dependency).
- `delta.characters_removed` - `original_size.characters - candidate_size.characters`.
- `delta.compression_ratio` - `candidate_size.characters / original_size.characters` (smaller is more compressed; e.g. `0.5` is a 50% reduction).

## Part 3 - The loop controller

The iterate-to-minimal loop. It drives the baseline and the candidate through the A/B gate, and on every regression lets the divergence tell it what was load-bearing.

### The loop

```
DEFINE   -> confirmed transfer set (transfer-set-design.md); user has signed off.
CAPTURE  -> teacher baseline, once, cached on (document_hash, transfer_set_hash) (Part 1).
COMPRESS -> regenerate a smaller candidate: core->pointer, de-dup, inert-prose removal (Part 2).
VALIDATE -> call skill-forge A/B equivalence (ab-equivalence.md) on (original, candidate, transfer set),
            passing the cached teacher transcripts so only the candidate is re-run this round.
  | on regression (any case candidate-regressed):
  |     the behaviour_delta NAMES the load-bearing behaviour that was lost.
  |     ADD BACK the minimum that restores it (see add-back mechanism), then re-VALIDATE.
  | on no regression (zero candidate-regressed):
  |     check diminishing returns -> shrink again (back to COMPRESS) or CONVERGE.
CONVERGE -> output the minimal passing candidate + the A/B distillation report.
```

VALIDATE is the only acceptance gate. A candidate that has not been through it is never output - the hard rule above. The A/B call returns the schema in `ab-equivalence.md`: per-case `verdict` (`equivalent` / `candidate-regressed` / `candidate-diverged`) and a `summary` whose `pass` is true iff zero cases regressed. Divergences do not fail the run - they are surfaced for the user's judgement, not treated as regressions.

### Add-back mechanism

A regression is a signal, not a dead end: the judge's `behaviour_delta` for a `candidate-regressed` case **names the specific behaviour lost**. Map it back and restore the minimum:

1. **Lost behaviour -> removed section.** The regressed case's `case_id` ties to the baseline's `disciplines_enforced` and to the transfer-set case's `exercises_sections`. Cross-reference: which section, dropped or over-compressed in the last COMPRESS, induced that behaviour in the baseline?
2. **Restore the minimum.** Add back the smallest piece of that section that restores the lost behaviour - prefer a pointer or a single bespoke detail over restoring the whole section. The goal is the minimal restoration, not reverting the compression.
3. **Re-validate.** Run A/B again. If the same behaviour is still lost, the restoration was insufficient - add back more. If a *different* behaviour regressed, treat it as a new add-back. Record each as a round.

The add-back is why the loop converges on a *minimal* document rather than just a *passing* one: it removes aggressively, then restores only what the A/B proves load-bearing.

### Convergence criteria

The loop stops on the first of:

- **Strict no-regression reached and no further shrink available** - the candidate passes (zero `candidate-regressed`) and the last shrink attempt could only reduce size by regressing. This is the target outcome: the minimal equivalent document.
- **Diminishing returns** - any further removal regresses some behaviour (every COMPRESS attempt from here trips the gate). The current passing candidate is the floor; stop and output it.
- **Budget ceiling** - a maximum number of candidate rounds (**default 5**). If the loop hits the ceiling with a passing candidate, output it and note in the report that the floor may not have been reached. If it hits the ceiling mid-add-back with no passing candidate, output the **original** (a distillation that cannot be proven equivalent is not shipped) and report the failure.

The gate is **behavioural equivalence, not textual similarity** - the loop never accepts a candidate because it "looks equivalent", only because the A/B run shows zero regression.

### Round-tracking schema

One object per round, for the A/B distillation report.

```json
{
  "round": 0,
  "candidate_size": {
    "characters": 0,
    "tokens_estimate": 0,
    "lines": 0
  },
  "ab_result": {
    "pass": true,
    "regressions": 0,
    "divergences": 0,
    "equivalents": 0
  },
  "action": "shrink|add-back|converge",
  "add_backs": ["string"],
  "hypothesis": "string",
  "outcome": "string"
}
```

- `round` - the round index (the baseline capture is round 0's precondition, not a round).
- `candidate_size` - the size schema from Part 2 for this round's candidate.
- `ab_result` - the `summary` block from the A/B capability's output (`ab-equivalence.md`): `pass` plus the verdict counts.
- `action` - what the controller did this round: `shrink` (regenerated smaller), `add-back` (restored a lost behaviour), or `converge` (accepted the candidate).
- `add_backs` - for an `add-back` round, the behaviours restored (the `behaviour_delta` strings the A/B named) and the sections they mapped to.
- `hypothesis` - what this round attempted (e.g. "drop the rationale paragraph in section 3 - baseline shows no case acted on it").
- `outcome` - what the A/B proved (e.g. "passed: paragraph was inert" or "regressed case adversarial-2: the soft rule was load-bearing").

### Budget math

The runner cost is the loop's dominant cost, and it is predictable:

- **Teacher baseline: `N` runner invocations**, where `N` is the case count - paid once, cached (Part 1).
- **Each candidate round: `N` runner invocations** - the candidate re-run; the teacher is never re-run (the caching rule). The equivalence judge runs once per case per round but is a judge, not a runner.
- **Total: `N * (1 + rounds)` runner invocations** - one baseline pass plus one candidate pass per round.

Report the **estimated** total at the start (`N * (1 + budget_ceiling)`, the worst case at the default 5-round ceiling) and the **actual** total at convergence (`N * (1 + rounds_run)`). A run that converges in 2 rounds costs `N * 3`, well under the `N * 6` worst case - the report states both so the cost is never hidden.

## Distribution-shift guard

A distillation is valid **only over the transfer set it was tested against** - the same overfitting risk model distillation carries outside its transfer distribution. The engine is honest about this and mitigates it two ways: it pushes for diverse, adversarial coverage at derivation time (the breadth of the set bounds the safety of the conclusion - see `transfer-set-design.md`), and it stays **conservative** - it keeps anything not *proven* behaviourally inert by the A/B, never dropping a span on the suspicion it is inert. Silence about untested behaviour is forbidden: the report names the coverage and states plainly that behaviour outside the transfer set is not guaranteed equivalent.
