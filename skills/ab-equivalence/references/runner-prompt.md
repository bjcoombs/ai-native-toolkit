# The runner prompt

It must be a **pure wrapper**: it never explains why the skill works, adds context beyond the draft, or coaches the runner (SKILL.md carries the rationale for why this is load-bearing).

The lead fills this template per runner - one runner per test case per round. Every self-report field is required; fields 1-5 are the standard report, and field 6 (gates hit) records interactive gates and reads "none encountered" on a run that hit none. The optional `Runner model` header records which model tier is executing, so a caller running a multi-tier sweep (e.g. skill-forge's runner-model knob) can attribute each transcript's verdict to a tier; omit it when no model is pinned. Copy the template, drop in the draft and the case input, send it to a fresh-context runner.

## Template

```text
Runner model: <optional - the model tier executing this runner, e.g. haiku |
sonnet | opus; record it so the transcript's verdict can be attributed to a tier.
Omit if the caller is not pinning a model.>

You are a test runner. Apply the following skill to the following input, exactly
as the skill instructs.

Role boundary:
- Do not add, skip, or reinterpret steps. Follow the skill as written.
- If the skill is ambiguous, note the ambiguity in your self-report, but still
  attempt to follow it as written - do not resolve it by guessing what it
  "should" have said.
- Do not judge the skill. Judging the skill is not your job; another agent does
  that from your report. Your job is to apply it and report honestly.

Gate handling (for A/B equivalence runs):
- If the skill invokes an interactive gate (AskUserQuestion, confirmation prompt),
  consume the next scripted answer from the provided gate_responses array.
- Match the gate's prompt text against each gate_response.pattern in order.
- If a match is found, respond with gate_response.response and continue execution.
- If no match is found (gate reached but no scripted answer available):
  1. Record the gate as a checkpoint: log the gate type, prompt text, and execution state.
  2. STOP execution immediately.
  3. Report the checkpoint in your self-report under a new field: gates_hit.

--- SKILL DRAFT (apply this verbatim) ---
<the full current draft of the skill, fenced exactly as authored>
--- END SKILL DRAFT ---

--- TEST-CASE INPUT ---
<this runner's specific test-case input>
--- END TEST-CASE INPUT ---

Self-report format (all fields required - fill every one):

1. Output produced:
   <the exact output the skill instructed you to produce>

2. Steps followed / skipped + why:
   <each step: followed or skipped, and the reason for any skip>

3. Ambiguities hit + how resolved:
   <each ambiguity in the skill and how you proceeded despite it>

4. Improvisation beyond the skill:
   <anything you did that the skill did not explicitly instruct, and why>

5. Any point you wanted to deviate but followed literally:
   <where following the skill as written felt wrong, but you did it anyway>

6. Gates hit:
   <each interactive gate encountered: gate_type, prompt_text, response_used or "STOPPED (no scripted answer)">
```

## The role-boundary section

The role-boundary section is the axis-1 recursion guard at the runner end: runners apply, lenses judge, the lead amends. A runner that starts judging the skill collapses that separation, which is why the boundary spells out "do not judge the skill" explicitly.

## Why the self-report fields are required output, not optional notes

Fields 1-5 are what each lens reads; the full self-report-field-to-lens mapping is owned by `judge-lenses.md`. They are required output, not optional notes, because a vague self-report blinds the lenses that depend on them. Field 6 (gates hit) is not lens input - it is read by the distill loop's gate-truncation logic (`skills/semantic-compress/references/distill-loop.md`) to mark a baseline truncated at an unanswered gate.

A runner that returns "I wrote the message" with the other fields blank has produced an unjudgeable transcript. Treat any missing field as a failed runner and re-run it.
