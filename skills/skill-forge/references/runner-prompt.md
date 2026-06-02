# The runner prompt

It must be a **pure wrapper**: it never explains why the skill works, adds context beyond the draft, or coaches the runner (SKILL.md carries the rationale for why this is load-bearing).

The lead fills this template per runner - one runner per test case per round. The five sections are all required. Copy the template, drop in the draft and the case input, send it to a fresh-context runner.

## Template

```
You are a test runner. Apply the following skill to the following input, exactly
as the skill instructs.

Role boundary:
- Do not add, skip, or reinterpret steps. Follow the skill as written.
- If the skill is ambiguous, note the ambiguity in your self-report, but still
  attempt to follow it as written - do not resolve it by guessing what it
  "should" have said.
- Do not judge the skill. Judging the skill is not your job; another agent does
  that from your report. Your job is to apply it and report honestly.

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
```

## The role-boundary section

The role-boundary section is the axis-1 recursion guard at the runner end: runners apply, lenses judge, the lead amends. A runner that starts judging the skill collapses that separation, which is why the boundary spells out "do not judge the skill" explicitly.

## Why the self-report fields are required output, not optional notes

These fields are what each lens reads; the full self-report-field-to-lens mapping is owned by `judge-lenses.md`. They are required output, not optional notes, because a vague self-report blinds the lenses that depend on them.

A runner that returns "I wrote the message" with the other fields blank has produced an unjudgeable transcript. Treat any missing field as a failed runner and re-run it.
