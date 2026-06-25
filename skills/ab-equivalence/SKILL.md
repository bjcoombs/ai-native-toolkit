---
name: ab-equivalence
description: "Compare two versions of an LLM-directed document - an original (teacher) and a candidate (student) - across a transfer set and return a per-case behavioural-equivalence verdict plus an efficiency signal. A transform-agnostic library capability other skills compose to gate a transform on behavioural sameness. TRIGGER when asked to A/B test two versions of a prompt / instruction / skill, to check whether a rewritten or compressed document still behaves the same as the original, to validate behavioural equivalence between two document versions, to gate a transform on no-regression, or when a skill needs the run-the-runner-on-both-versions-and-judge-equivalence capability."
---

# A/B equivalence - a transform-agnostic behavioural-equivalence capability

A thin capability that compares two versions of an LLM-directed document - an `original` (the teacher) and a `candidate` (the student) - across a transfer set, and returns a per-case verdict on whether the candidate still induces the behaviour the original induced.

It is **transform-agnostic**: it judges *behavioural equivalence between two versions* and neither knows nor cares which transform produced the candidate. It therefore serves every optimizer transform that claims to preserve behaviour - compression today, directive-clarity next - not just compression. It is a **library capability** other skills compose: `semantic-compress` invokes it to gate a distillation, and `skill-forge` exposes it alongside its own quality gate. It does **not** judge *absolute quality* ("is this skill good?") - that is a different question answered by different judges. A/B equivalence judges *sameness between two versions* ("does the candidate still do what the original did?").

This skill **owns the runner** (`references/runner-prompt.md`, the pure-wrapper template, paths relative to this skill directory). The runner is the shared execution primitive: it applies one version of a document to one case input and returns a transcript and self-report. Skills that need behavioural comparison compose this capability rather than re-implementing the runner.

The runner ships in **two variants**, both pure wrappers returning the same six self-report fields (see `references/runner-prompt.md`):

- the **skill variant** (default) - the document is invoked on demand against a case input;
- the **instruction-file variant** - the document is an *always-loaded* agent instruction file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursor/rules/*`, `.github/copilot-instructions.md`); the runner is handed only that file as its operating context plus a realistic repo task, and runs **read-only / sandboxed** (it states the actions it would take, never mutating the repo).

The variant is the caller's choice (skill-forge's artifact-type detection selects it); both produce a transcript the equivalence judge and skill-forge's lenses read identically.

## Input contract

| Input | Required | Notes |
|-------|----------|-------|
| `original` | yes | Path to the teacher document - the version whose behaviour is the equivalence target. |
| `candidate` | yes | Path to the student document - the transformed version under test. |
| `transfer_set` | yes | Array of cases spanning the test taxonomy (happy / edge / adversarial / composition). The transfer set *is* the operational definition of the behaviour being preserved, so its breadth bounds the safety of the conclusion. |

The caller (e.g. `semantic-compress`) owns deriving and confirming the transfer set; this capability consumes it. A thin transfer set yields a weak equivalence claim - the caller is responsible for flagging coverage, and the output records it.

## Mechanism

For each case in the transfer set:

1. Run the runner (`references/runner-prompt.md`, the pure-wrapper prompt) once with `original` as the skill draft, on the case input, producing the **teacher transcript**.
2. Run the same runner once with `candidate` as the skill draft, on the **identical** case input, producing the **candidate transcript**.
3. Hand both transcripts to the **equivalence judge** (`references/equivalence-judge-prompt.md` - a focused compare-two-transcripts judge), which emits the per-case verdict and efficiency signal.

The runner and runner-prompt are the only execution primitive; the equivalence judge is the one comparison component, distinct from any absolute-quality lens. The judge compares observed behaviour to observed behaviour, never the candidate against what the original document *says* it should do. The full contract and schema are in `references/ab-equivalence.md`; the judge prompt and decision rule are in `references/equivalence-judge-prompt.md`.

### Baseline caching (the teacher is captured once)

The `original` never changes across a multi-round transform loop, so its transcript per case is **captured once and reused across every round**. Only the candidate is re-run each round. This is a hard rule, not an optimization: re-running the teacher each round wastes runner budget and risks introducing teacher-side noise that the judge would mistake for a candidate change. The caller passes the cached teacher transcripts back in on rounds >= 2; this capability re-runs only the candidate. A budget ceiling on candidate re-runs belongs to the caller's loop, not here.

## Verdict categories

Per case, the judge returns exactly one verdict:

| Verdict | Meaning | What it must cite |
|---------|---------|-------------------|
| `equivalent` | The candidate induced every behaviour and discipline the original induced. Incidental wording differences with no behavioural consequence are still `equivalent`. | Nothing required beyond the verdict. |
| `candidate-regressed` | A behaviour or discipline the original induced is **absent** in the candidate. This is the failing verdict. | The **specific behaviour lost** - the discipline, step, or output the original produced and the candidate did not. |
| `candidate-diverged` | The candidate behaves **differently** but no behaviour the original induced was lost - a different-but-not-worse change (including incidental improvements). | The **difference** - what the candidate did differently. Not necessarily worse; documented for the caller's judgement. |

The regressed-vs-diverged decision is the load-bearing distinction, stated authoritatively in `references/equivalence-judge-prompt.md`:

- **regressed** = a behaviour or discipline the original induced is **missing** from the candidate (essence lost).
- **diverged** = the candidate did something **different**, but every behaviour the original induced is **still present** (nothing lost).

Decision order: check for any loss first. If any loss exists, the verdict is `candidate-regressed` - even alongside an unrelated gain; a regression is never excused by an improvement elsewhere. Only with no loss do you choose between `diverged` and `equivalent`. When uncertain whether a delta is a loss or merely a difference, the judge defaults to `candidate-regressed` - a false regression costs one add-back round; a false `equivalent` ships a behaviour-losing transform undetected.

## Efficiency signal (alongside every verdict)

Independent of the verdict, the judge records an **efficiency signal** per case - how directly the runner acted on each version versus how much it had to unpack or reinterpret the instruction before acting:

| Field | Type | Meaning |
|-------|------|---------|
| `original_directness` | integer 1-5 | How directly the runner acted on the **original**: 5 = acted immediately, no reinterpretation; 1 = had to unpack, infer, or work around the instruction heavily before acting. |
| `candidate_directness` | integer 1-5 | The same measure for the **candidate**. |
| `interpretation_notes` | string | What the runner had to unpack or reinterpret on each version - the qualitative evidence behind the two scores. |

The signal is read from the runner self-report (`references/runner-prompt.md`): *steps followed / skipped*, *ambiguities hit and how resolved*, *improvisation beyond the skill*, and *any point it wanted to deviate but followed literally* all reveal how much interpretive work each version forced. Directness is scored from interpretive work shown, **not** from document length - a shorter document that forced more reinterpretation is *less* direct, not more.

Why it exists: compression's gate is **strict no-regression** (sameness alone). But the optimizer family includes transforms that claim *behaviour-preserving-but-lighter* - directive-clarity rewrites instructions the model must unpack into directives that name the action. Such a transform can only be validated if the harness **measures the lightness, not just the sameness**: its gate is no-regression **and** a measured efficiency gain (`candidate_directness` > `original_directness` with no `candidate-regressed`). Recording the signal here, on every A/B run, is what lets those transforms prove a measured gain instead of asserting one. This capability records the signal; it never gates on it - whether a gain is required is the calling transform's gate.

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

## Execution modes

<!-- chat-replace:execution-mode-rule -->
The capability runs the same mechanism in every mode; modes differ only in how the runner pair per case and the equivalence judge are spawned. The caller's harness selects the mode; A/B equivalence runs inside whatever mode it is handed.

**Solo mode** (chat / standalone ZIP, no subagents) is the default: a single agent works each case sequentially - it applies the `original` via the runner wrapper, then the `candidate` on the identical input, then judges the two transcripts with the equivalence-judge prompt, recording the verdict and efficiency signal before moving to the next case. The cached teacher transcript is the only state carried between rounds.

<!-- chat-skip:start -->
**Phased sub-agent mode** (Agent Teams flag off): the lead spawns a fresh runner subagent per version per case (the runner pair) and a fresh judge subagent per case. With no persistent agents, the cached teacher transcripts are injected into each round so only the candidate is re-run.

**Team mode** (Agent Teams flag on, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`): the equivalence judge can run as a persistent background teammate (`Agent` with `run_in_background: true`, joined to the session's single implicit team), communicating with the lead via `SendMessage`; ephemeral runners are spawned per round and shut down after. As with the forge loop, shut each teammate down with a `SendMessage` shutdown_request at the end of the run; nothing persists to block a future run. Send `shutdown_request` **once**; the teammate approves with a structured `shutdown_response` (addressed to `team-lead`, echoing the `request_id`, `approve: true`), which terminates it. Treat that approval, or an already-exited teammate, as the completion signal; any that linger reap when the session exits.

No persistent team is required by the capability itself - the judge holds no across-round memory of its own; the cached teacher transcript is the only state carried between rounds, and the caller owns it.
<!-- chat-skip:end -->

## What this capability does not do

- It does not derive or confirm the transfer set - the caller does, and signs off on coverage.
- It does not decide whether the candidate is good in absolute terms - that is an absolute-quality gate, a different run.
- It does not add back lost behaviour or regenerate candidates - on a regression it names what was lost; the caller's loop acts on that.
- It does not gate on the efficiency signal - it records it. Whether a measured efficiency gain is required is the calling transform's gate, not this capability's.
