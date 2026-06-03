# The equivalence-judge prompt

A focused compare-two-transcripts judge, separate from the five quality lenses. The five lenses score one transcript against an intent ("is this skill good?"); this judge compares **two** transcripts against each other ("does the candidate still do what the original did?"). It is the only new component A/B equivalence adds - the runner and runner-prompt are reused unchanged.

It is filled once per transfer-set case: drop in the case input, the teacher transcript, and the candidate transcript, and send it to a fresh-context judge. The judge emits one structured verdict per case, conforming to the output schema in `ab-equivalence.md`.

## The comparison rubric

The judge does **not** read the two transcripts for prose similarity - textual sameness is irrelevant. It compares them on three behavioural dimensions, drawn from the runner self-report fields (`runner-prompt.md`):

1. **Outputs produced.** Did the candidate produce every output the original produced (the *output produced* field on both)? An output the original produced and the candidate did not is a loss.
2. **Disciplines enforced.** Did the candidate enforce every discipline, rule, or guard the original enforced (read from *steps followed / skipped*, *improvisation beyond the skill*, and *any point it wanted to deviate but followed literally*)? A discipline the original held the runner to and the candidate let slide is a loss.
3. **Steps followed.** Did the candidate lead the runner through the same load-bearing steps in a behaviourally-equivalent order (read from *steps followed / skipped*)? A step the original induced and the candidate dropped is a loss; a re-ordering with no behavioural consequence is not.

A behaviour counts as *induced by the original* only if it actually appears in the teacher transcript - the judge compares observed behaviour to observed behaviour, never the candidate against what the original document *says* it should do. (That latter question is Fidelity's, judged against intent, not this judge's.)

## The regressed-vs-diverged decision rule

The single load-bearing decision. Apply it after the three-dimension comparison:

- **`candidate-regressed`** - on any of the three dimensions, a behaviour, discipline, output, or step that the original **induced** is **absent** from the candidate transcript. Essence lost. Cite the specific behaviour lost in `behaviour_delta`. This is the only failing verdict.
- **`candidate-diverged`** - the candidate did something **different** on some dimension, but **every** behaviour the original induced is **still present** in the candidate transcript. Nothing the original did was lost; the candidate merely also did something else, or did it differently (including doing it *better*). Cite the difference in `behaviour_delta`. Not a failure - surfaced for the caller's judgement.
- **`equivalent`** - the candidate induced every behaviour the original induced and introduced no behaviourally-significant difference. Incidental wording differences with no behavioural consequence are `equivalent`, not `diverged`. `behaviour_delta` is empty.

Decision order: first check for any loss (a behaviour present in the teacher transcript, absent in the candidate). **If any loss exists, the verdict is `candidate-regressed`** - even if the candidate also improved elsewhere; a regression is never excused by an unrelated gain. Only if there is no loss do you choose between `diverged` (a behaviourally-significant difference remains) and `equivalent` (none does).

**Tie-break toward regression.** When you cannot decide whether a delta is a genuine loss or merely a difference, default to `candidate-regressed`. A false regression costs the caller one add-back round; a false `equivalent` ships a behaviour-losing transform undetected. The asymmetry is deliberate - the strict no-regression gate is only as trustworthy as this judge's conservatism.

## The efficiency signal

Alongside the verdict, score how directly the runner acted on each version - independent of whether behaviour was preserved:

- `original_directness` (1-5): how directly the runner acted on the **original** - 5 = acted immediately with no reinterpretation; 1 = had to unpack, infer, or work around the instruction heavily before acting.
- `candidate_directness` (1-5): the same measure for the **candidate**.
- `interpretation_notes`: what the runner had to unpack or reinterpret on each version, citing the *ambiguities hit*, *improvisation*, and *wanted to deviate but followed literally* self-report fields as evidence.

Score directness from how much interpretive work each transcript shows the runner doing **before** it could act - not from document length. A shorter document that forced more reinterpretation is *less* direct, not more. The verdict and the efficiency signal are independent: a candidate can be `equivalent` yet more direct (the case a lightness-claiming transform needs), or `equivalent` with no directness change (the typical compression case).

## Structured output

Emit one object per case, conforming to the `cases[]` element schema in `ab-equivalence.md`:

```json
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
```

- `behaviour_delta`: the specific behaviour lost (`candidate-regressed`), the difference observed (`candidate-diverged`), or empty (`equivalent`).
- Every field is required; a missing field makes the case unjudgeable. The caller aggregates these into the run-level `summary` (`pass` = zero `candidate-regressed`).

## Template

```text
You are an equivalence judge. You compare two runner transcripts produced from
the SAME input by two versions of a document - an ORIGINAL (teacher) and a
CANDIDATE (student) - and decide whether the candidate still induces the
behaviour the original induced. You are NOT scoring quality, and NOT comparing
either transcript against what the document says it should do. You compare
observed behaviour to observed behaviour.

--- TEST-CASE INPUT (identical for both runs) ---
<the case input both runners received>
--- END TEST-CASE INPUT ---

--- TEACHER TRANSCRIPT (from the ORIGINAL document) ---
<the original version's runner transcript and self-report>
--- END TEACHER TRANSCRIPT ---

--- CANDIDATE TRANSCRIPT (from the CANDIDATE document) ---
<the candidate version's runner transcript and self-report>
--- END CANDIDATE TRANSCRIPT ---

Compare on three dimensions: outputs produced, disciplines enforced, steps
followed. Then apply the decision rule:

- candidate-regressed: any behaviour/discipline/output/step the TEACHER
  transcript shows is ABSENT from the candidate. Essence lost. The only failing
  verdict. Cite the specific behaviour lost.
- candidate-diverged: the candidate did something different, but EVERY behaviour
  the teacher induced is still present. Nothing lost. Cite the difference.
- equivalent: every teacher behaviour present, no behaviourally-significant
  difference. Empty behaviour_delta.

Check for loss FIRST: any loss => candidate-regressed, even alongside an
unrelated gain. When unsure whether a delta is loss or mere difference, default
to candidate-regressed.

Also score the efficiency signal (independent of the verdict): how directly the
runner acted on each version (1-5), from how much it had to unpack/reinterpret
before acting - read the ambiguities, improvisation, and wanted-to-deviate
fields. Not from document length.

Emit exactly this JSON object (all fields required):

{
  "case_id": "<this case's id>",
  "verdict": "equivalent | candidate-regressed | candidate-diverged",
  "behaviour_delta": "<behaviour lost | difference observed | empty>",
  "efficiency_signal": {
    "original_directness": <1-5>,
    "candidate_directness": <1-5>,
    "interpretation_notes": "<what each version forced the runner to unpack>"
  }
}
```
