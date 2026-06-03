# A/B equivalence - a transform-agnostic behavioural-equivalence capability

A thin capability that compares two versions of an LLM-directed document - an `original` (the teacher) and a `candidate` (the student) - across a transfer set, and returns a per-case verdict on whether the candidate still induces the behaviour the original induced.

It is **transform-agnostic**: it judges *behavioural equivalence between two versions* and neither knows nor cares which transform produced the candidate. It therefore serves every optimizer transform that claims to preserve behaviour - compression today, directive-clarity next - not just compression. It is a **library capability** other skills compose: `semantic-compress` invokes it to gate a distillation. It does **not** change skill-forge's own five-lens quality gate hierarchy - that hierarchy judges *absolute quality* ("is this skill good?"); A/B equivalence judges *sameness between two versions* ("does the candidate still do what the original did?"). They are different questions answered by different judges.

## Input contract

| Input | Required | Notes |
|-------|----------|-------|
| `original` | yes | Path to the teacher document - the version whose behaviour is the equivalence target. |
| `candidate` | yes | Path to the student document - the transformed version under test. |
| `transfer_set` | yes | Array of cases drawn from the skill-forge test taxonomy (happy / edge / adversarial / composition - see `test-taxonomy.md`). The transfer set *is* the operational definition of the behaviour being preserved, so its breadth bounds the safety of the conclusion. |

The caller (e.g. `semantic-compress`) owns deriving and confirming the transfer set; this capability consumes it. A thin transfer set yields a weak equivalence claim - the caller is responsible for flagging coverage, and the output records it.

## Mechanism

For each case in the transfer set:

1. Run the **existing** runner (`runner-prompt.md`, unchanged - the same pure-wrapper prompt the forge loop uses) once with `original` as the skill draft, on the case input, producing the **teacher transcript**.
2. Run the same runner once with `candidate` as the skill draft, on the **identical** case input, producing the **candidate transcript**.
3. Hand both transcripts to the **equivalence judge** (`equivalence-judge-prompt.md` - a focused compare-two-transcripts judge, separate from the five quality lenses), which emits the per-case verdict and efficiency signal.

The runner and runner-prompt are reused verbatim; nothing about how a single version is executed changes. The only new component is the equivalence judge, which compares two transcripts rather than scoring one against an intent.

### Baseline caching (the teacher is captured once)

The `original` never changes across a multi-round transform loop, so its transcript per case is **captured once and reused across every round**. Only the candidate is re-run each round. This is a hard rule, not an optimization: re-running the teacher each round wastes runner budget and risks introducing teacher-side noise that the judge would mistake for a candidate change. The caller passes the cached teacher transcripts back in on rounds >= 2; this capability re-runs only the candidate. A budget ceiling on candidate re-runs belongs to the caller's loop, not here.

<!-- chat-skip:start -->
Execution follows the same mode selection as the forge loop (see SKILL.md): the runner pair per case can be spawned as parallel runner subagents (phased mode) or worked sequentially by a single agent (solo mode). The equivalence judge runs as one more focused judge in whatever mode the harness is in. No persistent team is required - the judge holds no across-round memory of its own; the cached teacher transcript is the only state carried between rounds, and the caller owns it.
<!-- chat-skip:end -->

## Verdict categories

Per case, the judge returns exactly one verdict:

| Verdict | Meaning | What it must cite |
|---------|---------|-------------------|
| `equivalent` | The candidate induced every behaviour and discipline the original induced. Incidental wording differences with no behavioural consequence are still `equivalent`. | Nothing required beyond the verdict. |
| `candidate-regressed` | A behaviour or discipline the original induced is **absent** in the candidate. This is the failing verdict. | The **specific behaviour lost** - the discipline, step, or output the original produced and the candidate did not. |
| `candidate-diverged` | The candidate behaves **differently** but no behaviour the original induced was lost - a different-but-not-worse change (including incidental improvements). | The **difference** - what the candidate did differently. Not necessarily worse; documented for the caller's judgement. |

The regressed-vs-diverged decision rule is the load-bearing distinction and is stated authoritatively in `equivalence-judge-prompt.md`:

- **regressed** = a behaviour or discipline the original induced is **missing** from the candidate (essence lost).
- **diverged** = the candidate did something **different**, but every behaviour the original induced is **still present** (nothing lost).

When uncertain whether a delta is a loss or merely a difference, the judge defaults to `candidate-regressed` - a false regression costs one add-back round; a false `equivalent` ships a behaviour-losing transform undetected.

## Efficiency signal (alongside every verdict)

Independent of the verdict, the judge records an **efficiency signal** per case - how directly the runner acted on each version versus how much it had to unpack or reinterpret the instruction before acting:

| Field | Type | Meaning |
|-------|------|---------|
| `original_directness` | integer 1-5 | How directly the runner acted on the **original**: 5 = acted immediately, no reinterpretation; 1 = had to unpack, infer, or work around the instruction heavily before acting. |
| `candidate_directness` | integer 1-5 | The same measure for the **candidate**. |
| `interpretation_notes` | string | What the runner had to unpack or reinterpret on each version - the qualitative evidence behind the two scores. |

The signal is read from the runner self-report (`runner-prompt.md`): *steps followed / skipped*, *ambiguities hit and how resolved*, *improvisation beyond the skill*, and *any point it wanted to deviate but followed literally* all reveal how much interpretive work each version forced.

Why it exists: compression's gate is **strict no-regression** (sameness alone). But the optimizer family includes transforms that claim *behaviour-preserving-but-lighter* - the next one, directive-clarity, rewrites instructions the model has to unpack into directives that name the action. Such a transform can only be validated if the harness **measures the lightness, not just the sameness**: its gate is no-regression **and** a measured efficiency gain (`candidate_directness` > `original_directness` with no `candidate-regressed`). Recording the signal here, on every A/B run, is what lets those future transforms prove a measured gain instead of asserting one. Compression ignores the gain and gates on no-regression alone; both read the same signal.

## Output schema

```json
{
  "cases": [
    {
      "case_id": "string",
      "verdict": "equivalent|candidate-regressed|candidate-diverged",
      "behaviour_delta": "string",
      "efficiency_signal": {
        "original_directness": 1,
        "candidate_directness": 1,
        "interpretation_notes": "string"
      }
    }
  ],
  "summary": {
    "pass": true,
    "regressions": 0,
    "divergences": 0,
    "equivalents": 0
  }
}
```

- `case_id` - the transfer-set case identifier.
- `verdict` - one of the three categories above.
- `behaviour_delta` - for `candidate-regressed`, the specific behaviour lost; for `candidate-diverged`, the difference observed; empty (or `""`) for `equivalent`.
- `efficiency_signal` - the per-case directness scores and notes described above.
- `summary.regressions` / `divergences` / `equivalents` - counts of each verdict across `cases`.
- **`summary.pass` is `true` if and only if zero cases are `candidate-regressed`.** Divergences do not fail the run - they are surfaced for the caller's judgement. This encodes the strict no-regression gate: the candidate is accepted only when it loses nothing.

## What this capability does not do

- It does not derive or confirm the transfer set - the caller does, and signs off on coverage.
- It does not decide whether the candidate is good in absolute terms - that is the five-lens forge gate, a different run.
- It does not add back lost behaviour or regenerate candidates - on a regression it names what was lost; the caller's loop acts on that.
- It does not gate on the efficiency signal - it records it. Whether a measured efficiency gain is required is the calling transform's gate, not this capability's.
