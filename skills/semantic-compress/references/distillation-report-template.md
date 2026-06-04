# The A/B distillation report

The report is the **evidence** that a distillation was validated - the Hard Rule made auditable. A compressed document ships only with this report alongside it; the report records what was tested, what the A/B proved, and the exact boundary of the equivalence claim. It is not optional narrative: it is the proof that the smaller document behaves the same, and the honest statement of where that proof stops.

## Persistence

Save the report as `<document-name>-distillation-report.md` **alongside the output document**. For a distilled skill, that is `skills/<skill>/references/` (sitting next to the skill's other reference docs). The report travels with the artifact it validates so the evidence is never separated from the compressed document.

## Mandatory vs optional fields

| Field | Status | Why |
|-------|--------|-----|
| Size Delta | **Mandatory** | The headline metric - what the compression bought. |
| Transfer Set + coverage | **Mandatory** | What was tested; the operational definition of essence. |
| Per-Case Equivalence Verdicts | **Mandatory** | The behavioural evidence the compression passed. |
| Per-Case Directness (efficiency signal) | **Mandatory for directive-clarity, optional for compress** | The `original_directness`->`candidate_directness` the A/B harness records per case. Directive-clarity *gates* on a measured directness gain, so the report must record it or the gate is unauditable; compress does not gate on it, so the column is informational there. |
| Gate-Truncated Coverage | **Mandatory if any gate-truncation** | Honest about partial measurement where a case stopped at a gate. |
| Distribution-Shift Caveat | **Mandatory** | The honesty clause - the boundary of the claim. Stated verbatim, never edited away. |
| What Was Dropped | Optional | Include if anything was removed (almost always). |
| What Proved Load-Bearing | Optional | Include if any add-back occurred, and to list uncovered-but-kept sections. |
| Per-Round Log | Optional | Include for multi-round runs. |

A report missing any mandatory field is incomplete and the distillation is not validated. The caveat in particular is **never** dropped or softened: a distillation with no stated boundary is a distillation claiming universal validity it does not have.

## Template

```markdown
# A/B Distillation Report: <document name>

**Run date:** <ISO date>  **Mode:** distill  **Verdict:** <PASS|FAIL><add ` (gate-truncated)` if any case was truncated at a gate>  **Rounds:** <N>
**Coverage:** <sections_covered>/<sections_identified> sections exercised (<X>%)

## Size Delta

| Metric | Original | Final | Δ |
|--------|----------|-------|---|
| Characters | N | M | -K (X%) |
| Estimated tokens | N | M | -K (X%) |
| Lines | N | M | -K |

Estimated tokens = characters / 4 (a coarse proxy, no tokenizer dependency).

## Transfer Set

| Case ID | Type | Input summary | Exercises sections |
|---------|------|---------------|--------------------|
| T1 | happy | ... | sections A, B |
| T2 | edge | ... | section C |
| T3 | adversarial | ... | section D |
| T4 | composition | ... | section E |
| ... | ... | ... | ... |

**Coverage:** N/M sections exercised (X%)
**Thin-coverage warnings:** <one line per uncovered/under-covered section: the section name and the behaviour that went untested - or "none">

## Gate-Truncated Coverage

<Include this section whenever any case was gate-truncated; omit it only when no case hit an unanswered gate.>

| Case | Gate Type | Truncation Point | Behaviour Beyond Gate |
|------|-----------|------------------|----------------------|
| T3 | AskUserQuestion | "Install scc?" | NOT MEASURED |
| T5 | AskUserQuestion | "Skip permanently?" | NOT MEASURED |

**⚠️ Coverage was gate-truncated.** The A/B equivalence verdict applies only to behaviour before the gates listed above. Behaviour triggered by user responses to these gates was not measured in this run.

To extend coverage: add `gate_responses` entries for these gates and re-run the distillation.

## Per-Case Equivalence Verdicts

| Case | Verdict | Directness (orig->cand) | Behaviour delta |
|------|---------|-------------------------|-----------------|
| T1 | equivalent | 3->3 | - |
| T2 | equivalent | 3->3 | - |
| T3 | candidate-diverged | 3->4 | <what differed, not worse - documented for judgement> |
| T4 | equivalent | 3->3 | - |

Verdicts are `equivalent` | `candidate-regressed` | `candidate-diverged`. **PASS iff zero `candidate-regressed`.** A `candidate-diverged` case is surfaced for the user's judgement, not a failure. Any `candidate-regressed` that survives to the final candidate is a FAIL - the original ships instead.

## What Was Dropped (Proven Behaviourally Inert)

<List each section/phrase removed and the evidence it was inert: the baseline shows no case's behaviour depended on it. This is the only license to delete - evidence-gated, never a guess.>

## What Proved Load-Bearing (Kept or Added Back)

<List each section that initially seemed droppable but was restored after an A/B regression named it - with the case that regressed and the behaviour it lost.>

<Also list each section **kept (uncovered, conservative default)**: never exercised by any case, so not proven inert, so kept by the distribution-shift guard's conservative default.>

## Per-Round Log

| Round | Action | Size (chars) | A/B result | Hypothesis | Outcome |
|-------|--------|--------------|------------|------------|---------|
| 1 | shrink | M1 | pass | drop rationale para in §3 - baseline shows no case acted on it | passed: inert |
| 2 | shrink | M2 | regressed (1) | drop the soft rule in §5 | regressed adversarial-2: soft rule was load-bearing |
| 3 | add-back | M3 | pass | restore §5 soft-rule pointer | passed: minimal restoration sufficient |
| ... | ... | ... | ... | ... | ... |

**Runner budget:** estimated N×(1+ceiling) invocations; actual N×(1+rounds_run). <state both>

## Distribution-Shift Caveat

**This distillation is valid over the transfer set above.** Behaviour outside this coverage is not guaranteed equivalent. The transfer set is the operational definition of the document's essence for this run - a smaller transfer set is a narrower guarantee. Sections never exercised by any case were kept by conservative default, not proven removable.
```

## Field notes

- **Verdict (header).** `PASS` only if the final candidate has zero `candidate-regressed` cases. If the loop hit its budget ceiling mid-add-back with no passing candidate, the verdict is `FAIL` and the **original** is shipped (a distillation that cannot be proven equivalent is not output) - the report states this.
- **Coverage % (header and Transfer Set).** `sections_covered / sections_identified`. Below 70%, the run should have warned before proceeding (the coverage threshold in the Distribution-Shift Guard); if it proceeded anyway, the report says so.
- **Behaviour delta.** For a regressed or diverged case, name the specific behaviour - the discipline, step, or output that changed. "Different" is not enough; the report names *what*.
- **Directness (orig->cand).** The per-case `original_directness`->`candidate_directness` (1-5) the A/B harness records on every run (`skills/ab-equivalence/references/ab-equivalence.md`). For a **directive-clarity** run this column is the audit trail for the gated directness gain (`candidate_directness` > `original_directness` with no `candidate-regressed`) - omitting it makes the gate unauditable, so it is mandatory there. For a **compress** run the gain is not required; the column is informational and typically reads unchanged (e.g. `3->3`).
- **Conservative-default sections.** Every section not exercised by any case is listed under What Proved Load-Bearing as "kept (uncovered, conservative default)" - this is how the report makes the guard's conservatism legible rather than silent.
- **Gate-truncated coverage (the honest-degrade rules).** When a case stopped at a gate with no scripted answer (`gate_truncated` in `distill-loop.md`), the report must degrade loudly, never silently:
  - The **verdict header** carries the `(gate-truncated)` suffix if any case was truncated, e.g. `**Verdict:** PASS (gate-truncated)`. The suffix is mandatory whenever the Gate-Truncated Coverage table is non-empty.
  - `sections_covered` **excludes** any section reachable only beyond a gate-truncation point - a section behind an unanswered gate was not measured and must not inflate the coverage count or percentage.
  - `thin_coverage_warnings` **includes** one line per such section: "Section X is reachable only after gate G; not measured in this run."
