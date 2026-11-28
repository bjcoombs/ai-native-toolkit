# Definition of ready

When an issue in this repository is ready for an agent to pick up, and what the two triage labels mean. Back to the [Map of Content](../index.md).

Most issues here are filed by agents with `gh issue create --body` or the REST API, which bypass the issue forms under `.github/ISSUE_TEMPLATE/` (relative to the repository root). This document, not the forms, is therefore the source of truth: the refinement script (`skills/refine/scripts/refine.py`) checks issue bodies against it, and the forms only ask humans in the web UI for the same sections.

## Machine-readable definition

The refinement script reads this block and holds no second copy of it. It is the only fenced `yaml` block in this file; `tests/test_issue_forms.py` fails if a second one appears or if the block loses a section or a label.

```yaml
version: 1
# The six section headings, in order. A section is present when a line is a
# Markdown heading at one of `heading_levels` whose text equals the name,
# compared after trimming and ignoring case, and at least one line follows it
# before the next heading of the same or a higher level that is neither blank nor one of `empty_markers`.
# Issue forms render `###`, and an optional form field left empty as
# `_No response_`; the regulator template agents write uses `##`.
sections:
  - Problem Statement
  - Technical Context
  - Impact
  - Root Cause
  - Solution Requirements
  - Success Criteria
heading_levels: [2, 3]
empty_markers:
  - _No response_
# Fibonacci story points. A larger issue is ready only as a parent whose
# sub-issues carry the work.
max_points: 8
size_pattern: 'Size: (\d+) points?'
# The sizing marker: a comment whose first line matches this records a
# sizing judgement (see `sized` below). The newer of it and a body
# `size_pattern` line wins.
size_marker: '<!-- refine:size (\d+) -->'
labels:
  triage: needs-triage
  ready: agent-ready
# One entry per checklist item below, keyed by the item's heading.
# `script`: the refinement script decides it. `judgement`: a person, or the
# refiner model under the rule in "Who may apply agent-ready", decides it.
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

The body has the six sections in `sections`, each with content: Problem Statement, Technical Context, Impact, Root Cause, Solution Requirements, Success Criteria. A clarified-scope comment (first line `<!-- triage:clarified-scope -->`) may supply a section the body lacks or replace the body's version of a section it contains, and the newest comment holding a section wins.

**Check: script.** Heading match as the block describes; order is not checked. A section whose only content is `_No response_`, which GitHub writes for an empty optional form field, counts as missing.

### `criteria-checkable`

Every item under Success Criteria is checkable by a command or a test, and names that command or test with its expected result. "Works well" is not a criterion; "`make check` exits 0" is. A criterion that can only be checked after merge says so.

**Check: judgement.** The script may flag a criterion that holds no inline code as a hint, but whether a command actually checks the claim needs a reader.

### `sized`

The issue is sized at 8 points or fewer, or it is decomposed: it has sub-issues that carry the work, each meeting this checklist on its own.

The size comes from one of two producers, and the newer one wins:

- **A sizing comment.** `/issues` triage and the refiner record each sizing judgement as an issue comment whose first line is `<!-- refine:size N -->` (`size_marker`), followed by a one-line reason. To resize, post a new comment; the newest one counts.
- **A body line.** `Size: <n> points` (`size_pattern`) in the issue body, as an author writes it. The body is dated by the issue's creation, because the API reports no time for a body edit, so any sizing comment is newer than the body line.

**Check: script.** The refinement script (`skills/refine/scripts/refine.py`) reads both producers and the issue's sub-issues. A size above `max_points` with no sub-issues fails. An issue with no size from either producer and no sub-issues reads `unsized`: it needs sizing, which is a judgement for `/issues` triage or the refiner, never a not-ready verdict of its own.

### `blockers-recorded`

Every issue or pull request this one depends on is recorded as a blocked-by edge (`gh api repos/<owner>/<repo>/issues/<n>/dependencies/blocked_by`), not only named in prose. An issue may be ready while its blockers are still open; the edge is what lets the marathon order the work.

**Check: judgement.** The script lists the edges and fails an issue whose text says "blocked by" or "depends on" (not negated) an open issue that has no edge; a closed one is only a hint, but whether a dependency exists that nothing records needs a reader.

### `no-open-question`

Nothing in the body or comments waits on a human call: no unanswered question to the maintainer, no "option A or B" left open, no decision a research pass cannot settle from the repository.

**Check: judgement.**

## Labels

- **`needs-triage`**: the issue has not been triaged. `.github/workflows/issue-needs-triage.yml` applies it when an issue opens, unless the issue carries `agent-ready` by the time the job reads its labels (a few seconds after it opens). It is removed when the issue is triaged: replaced by `agent-ready`, or kept with a comment that states the open question when triage finds one.
- **`agent-ready`**: every item in the ready checklist holds. An agent may start work on it.

### Who may apply `agent-ready`

- **The maintainer**, at any time.
- **The refiner**, only when the script-checked items pass and research leaves nothing that needs a human call. Every other issue goes into one batched question list on a single tracking issue. The maintainer vetoes a refiner decision by removing the label. This rule comes from the backlog refinement plan in [#1317](https://github.com/bjcoombs/ai-native-toolkit/issues/1317).

The refiner workflows run only while the repository variable `REFINE_ENABLED` is `true`, and run as a dry-run, writing nothing, until the variable `REFINE_MODE` is `live`. Setting `REFINE_MODE` to `live` turns on its writes; unsetting it returns to dry-run, and setting `REFINE_ENABLED` to `false` stops every job. [Backlog refinement](../refine.md) has the setup and the credentials.

The `/issues` triage pass applies `agent-ready` by its own sizing judgement today. Making it apply this same checklist is part of [#1317](https://github.com/bjcoombs/ai-native-toolkit/issues/1317).
