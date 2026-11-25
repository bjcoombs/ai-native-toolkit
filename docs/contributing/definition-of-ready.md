# Definition of ready

When an issue in this repository is ready for an agent to pick up, and what the two triage labels mean. Back to the [Map of Content](../index.md).

Most issues here are filed by agents with `gh issue create --body` or the REST API, which bypass the issue forms under `.github/ISSUE_TEMPLATE/` (relative to the repository root). This document, not the forms, is therefore the source of truth: the grooming script checks issue bodies against it, and the forms only ask humans in the web UI for the same sections.

## Machine-readable definition

The grooming script reads this block and holds no second copy of it. It is the only fenced `yaml` block in this file; `tests/test_issue_forms.py` fails if a second one appears or if the block loses a section or a label.

```yaml
version: 1
# The six section headings, in order. A section is present when a line is a
# Markdown heading at one of `heading_levels` whose text equals the name,
# compared after trimming and ignoring case, and at least one non-blank line
# follows it before the next heading. Issue forms render `###`; the regulator
# template agents write uses `##`.
sections:
  - Problem Statement
  - Technical Context
  - Impact
  - Root Cause
  - Solution Requirements
  - Success Criteria
heading_levels: [2, 3]
# Fibonacci story points. A larger issue is ready only as a parent whose
# sub-issues carry the work.
max_points: 3
size_pattern: 'Size: (\d+) points?'
labels:
  triage: needs-triage
  ready: agent-ready
# One entry per checklist item below, keyed by the item's heading.
# `script`: the grooming script decides it. `judgement`: a person, or the
# groomer model under the rule in "Who may apply agent-ready", decides it.
checklist:
  - id: sections-present
    check: script
  - id: criteria-checkable
    check: judgement
  - id: sized
    check: script
  - id: blockers-recorded
    check: judgement
  - id: no-open-question
    check: judgement
```

## Ready checklist

An issue is ready when every item holds. Each heading is the item's `id` in the block above.

### `sections-present`

The body has the six sections in `sections`, each with content: Problem Statement, Technical Context, Impact, Root Cause, Solution Requirements, Success Criteria. A clarified-scope comment (first line `<!-- triage:clarified-scope -->`) may supply a section the body lacks.

**Check: script.** Heading match as the block describes; order is not checked.

### `criteria-checkable`

Every item under Success Criteria is checkable by a command or a test, and names that command or test with its expected result. "Works well" is not a criterion; "`make check` exits 0" is. A criterion that can only be checked after merge says so.

**Check: judgement.** The script can flag a criterion that holds no inline code as a hint, but whether a command actually checks the claim needs a reader.

### `sized`

The issue is sized at 3 points or fewer (a `Size: <n> points` line matching `size_pattern`, at most `max_points`), or it is decomposed: it has sub-issues that carry the work, each meeting this checklist on its own.

**Check: script.** It reads the size line and the issue's sub-issues from the API. With neither, the item is not met.

### `blockers-recorded`

Every issue or pull request this one depends on is recorded as a blocked-by edge (`gh api repos/<owner>/<repo>/issues/<n>/dependencies/blocked_by`), not only named in prose. An issue may be ready while its blockers are still open; the edge is what lets the marathon order the work.

**Check: judgement.** The script can list the edges, but whether a dependency exists that no edge records needs a reader.

### `no-open-question`

Nothing in the body or comments waits on a human call: no unanswered question to the maintainer, no "option A or B" left open, no decision a research pass cannot settle from the repository.

**Check: judgement.**

## Labels

- **`needs-triage`**: the issue has not been triaged. `.github/workflows/issue-needs-triage.yml` applies it when an issue opens, unless the issue opens already carrying `agent-ready`. It is removed when the issue is triaged: replaced by `agent-ready`, or kept with a comment that states the open question when triage finds one.
- **`agent-ready`**: every item in the ready checklist holds. An agent may start work on it.

### Who may apply `agent-ready`

- **The maintainer**, at any time.
- **The groomer**, only when the script-checked items pass and research leaves nothing that needs a human call. Every other issue goes into one batched question list on a single tracking issue. The maintainer vetoes a groomer decision by removing the label. This rule comes from the autonomous backlog groomer, [#1317](https://github.com/bjcoombs/ai-native-toolkit/issues/1317).

The `/issues` triage pass applies `agent-ready` under the same checklist when the maintainer runs it.
