# The runner prompt

It must be a **pure wrapper**: it never explains why the skill works, adds context beyond the draft, or coaches the runner (SKILL.md carries the rationale for why this is load-bearing).

The lead fills this template per runner - one runner per test case per round. Every self-report field is required; fields 1-5 are the standard report, and field 6 (gates hit) records interactive gates and reads "none encountered" on a run that hit none. The optional `Runner model` header records which model tier is executing, so a caller running a multi-tier sweep (e.g. skill-forge's runner-model knob) can attribute each transcript's verdict to a tier; omit it when no model is pinned. Copy the template, drop in the draft and the case input, send it to a fresh-context runner.

## Two runner variants

The runner has two variants. Both are pure wrappers, both return the **same six self-report fields**, and both carry the same axis-1 role-boundary guard (apply, do not judge). They differ only in *what* the runner is handed and what "apply it" means:

| Variant | When | What the runner is handed | What "apply" means |
|---------|------|---------------------------|--------------------|
| **Skill variant** (default) | The document under test is a skill (`SKILL.md`) or any draft *invoked on demand* against a case input | The skill draft + one test-case input | Execute the skill on the input and produce its output |
| **Instruction-file variant** | The document under test is an *always-loaded* agent instruction file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursor/rules/*`, `.github/copilot-instructions.md`) | **Only** this instruction file as operating context + one realistic repo task | Carry out the repo task the way the instruction file directs - but **read-only / sandboxed** |

The instruction-file variant exists because an always-loaded context file is never "invoked on an input" - it steers whatever the agent already does. So the runner is handed the file as its *only* context (no other repo conventions leaking in) plus a realistic repo task, and the transcript shows how a cold-start agent that trusted only that file would behave. The read-only / sandbox rule is what keeps a behaviour-probe from mutating the target repo: the runner **states the actions it would take** (commands it would run, files it would edit and how) rather than performing them; it may read files to inform those actions. That makes the runner safe to point at any real repo, and it still surfaces the load-bearing signal - the **accuracy** of the file's stated commands and paths - which the Fidelity lens reads from the *output produced* and *ambiguities* fields. The same selector that picks the variant is owned by the caller (skill-forge's artifact-type detection); this file owns both templates.

## Template - skill variant (default)

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

## Template - instruction-file variant (read-only)

Use this variant when the document under test is an always-loaded instruction file. It is the same pure wrapper with two changes: the document is the runner's *only* operating context (not a draft invoked on an input), and the runner is **read-only / sandboxed** - it states the actions it would take instead of performing them. The six self-report fields are unchanged so every lens reads them identically; "Output produced" now means the concrete actions and edits the runner would make (plus any artifact text it would author).

```text
Runner model: <optional - the model tier executing this runner, e.g. haiku |
sonnet | opus; record it so the transcript's verdict can be attributed to a tier.
Omit if the caller is not pinning a model.>

You are a test runner. The instruction file below is your ONLY operating context
for this run. Carry out the repo task below the way the instruction file directs.

Read-only / sandbox boundary (do not skip):
- Do NOT mutate the repo. State the actions you WOULD take - the exact commands
  you would run, and the files you would create or edit and how - instead of
  performing them.
- You MAY read any file in the repo to inform those actions. If the instruction
  file names a command or path, check whether it actually exists before relying
  on it, and record what you found.

Role boundary:
- Do not add, skip, or reinterpret what the instruction file directs. Follow it
  as written.
- If the instruction file is ambiguous, incomplete, or names something that does
  not exist, note that in your self-report, but still attempt to follow it as
  written - do not silently fill the gap from outside knowledge of the repo.
- Do not judge the instruction file. Judging it is not your job; another agent
  does that from your report. Your job is to apply it and report honestly.

--- INSTRUCTION FILE (your only operating context) ---
<the full current instruction file under test, fenced exactly as authored>
--- END INSTRUCTION FILE ---

--- REPO TASK ---
<this runner's specific realistic repo task>
--- END REPO TASK ---

Self-report format (all fields required - fill every one):

1. Output produced:
   <the concrete actions and edits you would take - commands you would run, files
   you would create/edit and how - plus any artifact text you would author>

2. Steps followed / skipped + why:
   <each directive you acted on or skipped, and the reason for any skip>

3. Ambiguities hit + how resolved:
   <each ambiguity, gap, or stated-but-missing command/path, and how you proceeded>

4. Improvisation beyond the instruction file:
   <anything you did that the file did not direct, and why>

5. Any point you wanted to deviate but followed literally:
   <where following the file as written felt wrong or build-breaking, but you did
   it anyway>

6. Gates hit:
   <each interactive gate encountered: gate_type, prompt_text, response_used or
   "STOPPED (no scripted answer)"; "none encountered" if none>
```

The accuracy signal lives in fields 1 and 3: a runner that "would run" a command the repo does not define, or "would edit" only the files the instruction file listed while the repo's tooling enforces more, exposes exactly the divergence the Fidelity accuracy sub-check fails on (see `judge-lenses.md`).

## The role-boundary section

The role-boundary section is the axis-1 recursion guard at the runner end: runners apply, lenses judge, the lead amends. A runner that starts judging the document collapses that separation, which is why both variants spell out "do not judge" explicitly. The instruction-file variant adds the read-only / sandbox boundary on top: a behaviour-probe that mutated the target repo would be a second way the runner overstepped its lane.

## Why the self-report fields are required output, not optional notes

Fields 1-5 are what each lens reads; the full self-report-field-to-lens mapping is owned by `judge-lenses.md`. They are required output, not optional notes, because a vague self-report blinds the lenses that depend on them. Field 6 (gates hit) is not lens input - it is read by the distill loop's gate-truncation logic (`skills/semantic-compress/references/distill-loop.md`) to mark a baseline truncated at an unanswered gate.

A runner that returns "I wrote the message" with the other fields blank has produced an unjudgeable transcript. Treat any missing field as a failed runner and re-run it.
