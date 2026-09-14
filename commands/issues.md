---
name: issues
disable-model-invocation: true
description: GitHub-issue marathon - triage open issues, then run agent-ready ones to merge with Agent Teams
argument-hint: [scope-label] (optional - narrows which open issues are considered; default: all open issues)
---

<!-- floor:cold-verify-completion -->

# GitHub Issue Marathon

> Thin orchestrator. Triages open issues, then delegates execution of `agent-ready` issues
> to the marathon skill (same engine as `/tm`).

## Configuration

Read the repo's CLAUDE.md `## Marathon Configuration` (GitHub Issues subsection) for label
names, with defaults:
- Agent-ready label: `agent-ready`
- Needs-triage label: `needs-triage`
- In-progress label: `in-progress`
- Issue exclude labels: (none)

Also read base branch, required approvals, and bot-reviewer rules (shared with `/tm`).

## Phase 0: Capability Detection

```bash
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS   # set $TEAMS_AVAILABLE (true if "1")
```

## Routing

```bash
ORG=$(gh repo view --json owner --jq '.owner.login')
REPO=$(gh repo view --json name --jq '.name')
# Optional scope filter from $ARGUMENTS — narrows the issue universe; routing still applies within it.
SCOPE_LABEL="$ARGUMENTS"   # empty = all open issues
FILTER=(); [ -n "$SCOPE_LABEL" ] && FILTER=(--label "$SCOPE_LABEL")
READY=$(gh issue list --label "agent-ready" "${FILTER[@]}" --state open --json number --jq 'length')
```

- `READY > 0` → **Marathon mode.** Work ONLY the `agent-ready` issues. Do NOT assess or
  modify untagged issues — the human has curated the queue by tagging.
- `READY == 0` → **Triage mode** (below).

## Triage Mode (no agent-ready issues exist)

Enumerate open issues minus the exclude labels:
```bash
gh issue list "${FILTER[@]}" --state open --json number,title,body,labels | \
  jq '[.[] | select((.labels[].name) as $l | ($l | IN("<exclude-labels>")) | not)]'
```

If `$ARGUMENTS` (a scope label) was given, only issues carrying it are considered; routing still applies within that subset.

Triage is a planning pass, not a labeling pass. It runs once over the whole issue set, in
this order, and every write it makes shows up in the report the human approves:

1. **Promote on Confirmation** - fold in answers the human gave since the last run.
2. **Research Pass** - resolve what the repository can answer before asking anything.
3. **Overlap Sweep** - hold back issues that collide with open PRs; record issue-to-issue
   collisions as edges or hot-file notes.
4. **Size by Judgment** - decide one PR or several, and propose any decomposition.
5. **Dependency Authoring** - write the ordering as native `blocked_by` edges.
6. **Triage Report** - apply every label write at once, then render labels, decomposition
   tree, execution order, overlaps; STOP.

An issue is `agent-ready` only when it survives all of steps 2 to 4: clear after research,
no unresolved overlap, and sized to one PR (or approved as a decomposed parent). Everything
else stays or becomes `needs-triage`. Research and sizing read widely, so fan them out with
subagents (the `Agent` tool); never teammates.

Steps 1 to 4 decide the label of each issue that exists at the start of the pass but do not
write it: the verdict needs all four inputs, so every `agent-ready` / `needs-triage` label change
on those issues happens in one write at the Triage Report step. Creating an approved
decomposition is a separate phase after approval that labels its own children and parent (see
Size by Judgment). An interrupted pass therefore never leaves an issue `agent-ready` while it overlaps an open
PR. Comments, body edits and dependency edges are written where they are decided.

### Promote on Confirmation

Start with issues already labeled `needs-triage` that carry a triage comment with a
recommended reading, or a decomposition proposal comment (marker
`<!-- triage:decomposition-proposal -->`, see Size by Judgment). A human reply approving that
proposal on the issue counts as approval of the decomposition and triggers its creation phase. If a human reply after that comment confirms or corrects it ("yes,
reading A" is enough), fold the confirmed answers into the issue body (append a
`Clarified scope` section with the confirmed scope and acceptance criteria) or, when the body
is the author's to keep, into a marker-tagged issue comment: a new comment whose first line is the
HTML-comment marker `<!-- triage:clarified-scope -->`. The marker, not any UI pinning, is what
makes it durable: the implementing teammate and every later
run find the clarified contract by grepping for it, never by re-interpreting the thread. The
issue's verdict becomes `agent-ready` (label swapped from `needs-triage` at the Triage Report
step) unless the same pass's Overlap Sweep or Size by Judgment holds it back:

```bash
gh issue edit <N> --body-file <body-plus-clarified-scope.md>   # issue body route
gh issue comment <N> --body-file <clarified-scope.md>          # marker comment route; first line is the marker
```

A correction that opens a new question is not a confirmation: run the Research Pass on the
new question and keep `needs-triage`. No reply means no change.

### Research Pass

Before any question reaches a human, research it. For each issue whose scope, acceptance
criteria, or intent is unclear, run an understand-style pass over what the ambiguity
touches: the code and tests, open and merged PRs (`gh pr list --state all --search
"<terms>"`), and the issue history (linked, referenced, and closed issues). The primitive is
the `/understand` command (`commands/understand.md`, relative to the plugin root): define the
terms, separate the explicit need from the implicit one, and bound the scope, grounded in
what the repository shows. Spawn one research subagent per ambiguous issue (or per subsystem
for a wide one) so the pass runs in parallel.

Any question the repository answers is answered, not asked. What the code does now, whether a
constraint is real, which reading matches existing behaviour: these are findings with a
`path:line` reference. Only questions of intent, priority, or product direction survive.

Post a comment that proposes rather than interrogates, findings first:

```bash
gh issue comment <N> --body "$(cat <<'EOF'
Triage research before this can be picked up by an agent.

Findings:
- The code currently does X (`path/to/file.py:42`); PR #M changed it to Y.
- Reading A implies change P; reading B implies change Q.

Recommended reading: A, because <evidence with file references>. Confirm or correct.

Resolved by research:
- <question the repository answered> - <answer> (`path:line`)

Needs your call:
- <question only the author can answer: intent, priority, direction>
EOF
)"
```

Fail closed: an issue whose ambiguity survives research gets the `needs-triage` verdict. Research is never
a license to guess; a recommended reading is a proposal until a human confirms it. When research
resolves every question and nothing is left under `Needs your call`, the issue is clear and
continues to the Overlap Sweep without a comment round-trip.

### Overlap Sweep

List open PRs with their changed files once per triage run:

```bash
gh pr list --state open --limit 100 --json number,title,headRefName,files | \
  jq '[.[] | {number, title, headRefName, files: [.files[].path]}]'
```

For each candidate issue, take the files it targets (paths named in the body, those the
Research Pass located, or, for an issue that skipped research, those located by a quick read of
the code it describes) and flag any intersection:
- **File-path intersection** with an open PR's changed files.
- **Scope intersection** with an open PR's title or body (it partially fixes, obsoletes, or
  reverses what the issue asks), even with no shared file.
- **Issue-to-issue intersection** with another issue in this pass's candidate set, compared by
  its pending verdict (labels are not written until the Triage Report step).

An issue that overlaps an open PR gets the `needs-triage` verdict, not `agent-ready`, and a comment
naming the PR and stating what remains of the issue after that PR merges (or that nothing
does). A PR cannot be a native `blocked_by` blocker (see PR-as-blocker in the adapter), so this
label plus comment is the record; the next triage run after the PR merges re-assesses the issue.
Issue-to-issue overlap is not a hold: resolve it as a hot-file note or a dependency edge
(Dependency Authoring) and list it in the report.

### Size by Judgment

Sizing runs after clarification: an issue that is not yet clear cannot be sized.

**Size by judgment, record the reasoning.** Decide whether an issue is one PR or several by asking
two questions: could a reviewer verify the whole change against its acceptance criteria in one
sitting, and must any part land before another? Story points (1, 2, 3, 5, 8, 13) are the
vocabulary for stating that judgment, not a trigger for it. Decompose when splitting makes the
deliverable more reviewable or gives the marathon an ordering it would otherwise discover by
collision. Leave the issue whole when splitting would only add ceremony. The judgment sits with
triage because it holds the codebase context; the report makes it legible so the human can veto.

Every split and every deliberate non-split carries a one-line reason in the triage report, a
reason a reader can disagree with ("one file, one reviewer sitting" or "schema change must land
before the two consumers"). No hierarchy is created without a reason.

When decomposition is warranted:
1. **Propose, then wait for approval.** Show the decomposition tree (parent, each child's scope,
   the reason for the split, and the child order) in the triage report, and persist the same
   tree as a comment on the parent whose first line is `<!-- triage:decomposition-proposal -->`,
   so the proposal survives the session. The parent's verdict is `needs-triage` until approval.
   Nothing is created until the human approves, either by replying to proceed in the session or
   by replying on the parent after the proposal comment (picked up by Promote on Confirmation on
   the next triage run, which runs once the `agent-ready` queue drains).
2. **After approval, create the children** (the post-approval phase, outside the single label
   write of the Triage Report step), each scoped to one reviewable PR, and attach them to the parent:
   ```bash
   CHILD=$(gh issue create --title "<child title>" --body "<scope + acceptance criteria; Part of #<parent>>" | sed 's#.*/##')
   CHILD_ID=$(gh api repos/$ORG/$REPO/issues/$CHILD | jq '.id')     # numeric REST id
   gh api repos/$ORG/$REPO/issues/<parent>/sub_issues --method POST -F sub_issue_id="$CHILD_ID"
   gh issue edit "$CHILD" --add-label "agent-ready"
   gh issue edit <parent> --remove-label "needs-triage" --add-label "agent-ready"   # once, after all children exist
   ```
3. **Order the children.** Where one child must land before another, author a `blocked_by` edge
   between them (Dependency Authoring). Sub-issue position is display order only.
4. **The parent is QA, not work.** It keeps the deliverable-level acceptance criteria, is labeled
   `agent-ready` so the marathon sees it as a verification unit, and is never assigned as
   implementation work. No child PR says `Closes #<parent>`. The parent closes only after every
   child has merged and the lead-run verification pass confirms the assembled result meets the
   parent's criteria. Its `sub_issues_summary` (`total`, `completed`, `percent_completed` on
   `gh api repos/$ORG/$REPO/issues/<parent>`) gives the human the rollup.
5. **A parent sent back to `needs-triage`** (a marathon skip or a failed parent check) is
   re-assessed as a parent, not re-decomposed: resolve the named gap (missing children back to
   `agent-ready`, or a new child for a failed criterion, proposed for approval like any
   decomposition), and its verdict returns to `agent-ready` only when every open child is
   `agent-ready`.

### Dependency Authoring

When triage finds an ordering (one issue's change is a prerequisite for another's: schema before
consumer, contract before enforcement, or a decomposed parent's child sequence), record it as a
native edge so the marathon DAG inherits it instead of discovering it by merge conflict:

```bash
BLOCKER_ID=$(gh api repos/$ORG/$REPO/issues/<M> | jq '.id')     # numeric REST id, NOT the node id
gh api repos/$ORG/$REPO/issues/<N>/dependencies/blocked_by \
  --method POST -F issue_id="$BLOCKER_ID"                          # issue N is blocked by issue M
```

Check existing edges first (`gh api repos/$ORG/$REPO/issues/<N>/dependencies/blocked_by`) so the
pass does not duplicate one, and give each new edge a one-line reason in the report. Sequencing
behind an open PR is not an edge (PR-as-blocker is unsupported); that is the Overlap Sweep's
`needs-triage` hold. If the human vetoes an edge, remove it with the adapter's `--method DELETE`
call. Render the resulting order in the report as waves, so the human approves the sequence and
not just the membership.

### Triage Report

Apply the label verdicts from steps 1 to 4 in one sequential write per issue (never parallel
background writes), then **report and STOP** (mirrors `/tm` planning):

```bash
gh issue edit <N> --remove-label "needs-triage" --add-label "agent-ready"   # clear, no overlap, one PR
gh issue edit <N> --add-label "needs-triage"                                # research left a call, or overlap
```

An issue proposed for decomposition gets `needs-triage` (its proposal comment is the record) until the
decomposition is approved and created.

```
## Issue Triage: <org>/<repo>

Tagged agent-ready: #12, #15, #18
Tagged needs-triage:
- #20, #21: research posted (Needs your call: 1 each)
- #22: overlaps open PR #40 (both edit commands/issues.md); after it merges: <what remains>
Promoted on confirmation: #19 (reading A folded into the issue body)

Sizing:
- #12: one PR - single module, reviewable in one sitting
- #15: one PR - splitting the doc and test changes would only add ceremony
- #30: decompose (pending approval) - migration must land before the two consumers
  #30 parent: <deliverable acceptance criteria> (verification unit)
  ├─ child A: schema migration          (one PR)
  ├─ child B: consumer X, blocked by A  (one PR)
  └─ child C: consumer Y, blocked by A  (one PR)

Dependencies authored:
- #18 blocked by #15 - #15 adds the config key #18 reads

Execution order:
  Wave 1: #12, #15, child A
  Wave 2: #18, child B, child C
  After children merge: #30 parent verification

Overlaps:
- #22 <-> PR #40: commands/issues.md (held as needs-triage)
- #12 <-> #15: README.md (additive, left parallel)

Approve the decomposition of #30 and the execution order? Reply to proceed (this creates the
sub-issues), or re-run /issues to begin on the agent-ready issues.
```

With no decomposition proposed, the report ends instead with:
```
OK to start on the agent-ready issues in this execution order? Re-run /issues to begin, or reply to proceed.
```

Do NOT spawn teammates in triage mode.

## Marathon Mode (agent-ready issues exist)

### Entry Gate (non-removable)

Before starting the marathon run, before the first issue is decomposed, invoke
the acceptance-contract start gate. Enforcement is source-agnostic: a gate
reachable only via `/tm` would make `/issues` the pressure valve that routes work
around the contract, so `/issues` carries the same obligation. The run identifier
is the issue-queue identifier - the label/milestone slug for this queue, e.g.
`issues-<label>` (`issues-agent-ready` when no scope filter narrows it).

The contract scripts live in the plugin package (`${CLAUDE_PLUGIN_ROOT}/scripts/contract/`), while the contract artifacts (contract, kill test, completion record) live in the target repository's `.taskmaster/contract/`, the scripts' default `--contract-dir`. When `CLAUDE_PLUGIN_ROOT` is unset (a hand-placed checkout rather than an installed plugin) the guard line before each invocation falls back to the current checkout.

```bash
: "${CLAUDE_PLUGIN_ROOT:=.}"   # unset outside an installed plugin: fall back to the current checkout
python "${CLAUDE_PLUGIN_ROOT}/scripts/contract/start_gate.py" "issues-<label>"
```

The gate fails closed. Exactly two doors open a run; there is no silent third -
including for a heterogeneous issue queue:

- **Frozen contract** - freeze evidence for the queue's coherent deliverable is
  recorded (contract sha256 frozen before decomposition, kill test passed): exit
  0, proceed.
- **Operator-signed skip** - no valid freeze, but an `operator_signoff` is
  recorded before the run starts: exit 0 with a loud UNVERIFIED warning. The skip
  is capped, not free - the run is permanently capped at `UNVERIFIED` and can
  NEVER certify `PASS`.
- **Neither** - non-zero exit. Do NOT start the run: author a contract for the
  queue's deliverable and freeze it (`${CLAUDE_PLUGIN_ROOT}/scripts/contract/freeze.py`), or record a
  signed skip first.

The exit-side gates are owned by the marathon skill, not this command. Marathon
routes every verifier spawn through `${CLAUDE_PLUGIN_ROOT}/scripts/contract/spawn_verifier.py` (the
custody chokepoint) and blocks run-complete on `${CLAUDE_PLUGIN_ROOT}/scripts/contract/complete_gate.py
<run-id>`. Start gate here, exit gates there - each fails closed. The
`<!-- floor:cold-verify-completion -->` marker in this file's header makes this
invocation un-removable: `floor.yml` reds any PR that drops the marker or any of
the three gate invocation strings from a file that carried them.

### GitHub Work-Source Adapter

Supply the marathon skill's adapter as:
- **enumerate** — `gh issue list --label "agent-ready" "${FILTER[@]}" --state open --json number,title,body,labels`
  (scoped by the optional `$ARGUMENTS` label filter);
  dependencies from `gh api repos/$ORG/$REPO/issues/<N>/dependencies/blocked_by`
  (each blocker issue number is a dependency edge). Complexity: infer from issue body/labels.
  A pull request cannot be a `blocked_by` blocker (see PR-as-blocker below), so an
  open-PR blocker never arrives as a native edge: when an issue must wait for an open PR,
  record that sequencing in the run plan (the wave table) and hold the issue out of any
  wave until the PR merges.
  Read the edges and the rollup in the same call rather than one request per issue:
  `gh issue list ... --json number,title,body,labels,blockedBy,subIssues,subIssuesSummary,parent`
  returns the edges, the parent/child graph, and the rollup, so leaf and parent membership is a
  local lookup. The GraphQL-backed shapes differ from REST: `blockedBy` and `subIssues` arrive as
  `{nodes: [...], totalCount}`, not the flat array `dependencies/blocked_by` returns, so read
  `.blockedBy.nodes[].number` (reading the REST shape silently yields zero edges). The per-issue
  REST paths below stay the reference and the write side.
  Decomposed parents: an enumerated issue whose `sub_issues_summary.total` is above 0 (the
  `subIssuesSummary` field) is decomposed, never a work unit, and never handed to an
  implementing teammate. The work units are the enumerated leaf issues: children from
  `.../sub_issues` count only when they are also in the enumerated set (open, `agent-ready`,
  matching the scope filter), plus every undecomposed enumerated issue. An untagged or
  out-of-scope child is never pulled in by its parent, and a child the Staleness Check dropped
  stays dropped for this run. A child counts as done only when verified `CLOSED` by a merged PR:
  `gh issue view <child> --json state,closedByPullRequestsReferences` shows `state` `CLOSED`, and
  at least one referenced PR number resolves to `MERGED` via `gh pr view <pr> --json state`.
  The reference list alone is not enough: it includes open PRs and carries no merge state. `sub_issues_summary` counts closures of any
  kind, so it is the human rollup, not the trigger.
  At plan time, compare each decomposed parent's children against the enumerated set. Exactly
  one of two outcomes applies, and the second is a catch-all:
  - **Verification unit** - every child is either in this run or done. The parent becomes
    eligible when the last one is done, a state rather than only an event: a parent whose
    children were all done before this run is eligible before Wave 1.
  - **Skip** - anything else (a child open but not in this run, a child closed without a merged
    PR, no child in the run and at least one child not done). Report it ("#N: 1 of 2 children in this run; verification
    deferred", "#N: child #M closed without a merged PR") and swap the parent's label from
    `agent-ready` to `needs-triage` with a comment naming the children at fault, so a parked
    parent never keeps `READY` above 0 and pins `/issues` in Marathon mode. The next triage run
    re-assesses it (see Size by Judgment); its in-run children still run.
  Parent closure is gated on the lead-run QA pass over the merged children, not on `Closes #N`
  in any single PR. That check runs outside `spawn_verifier.py` and adds no per-parent freeze:
  the run-level contract, its freeze, the custody chokepoint, and the completion gate are
  unchanged. "Lead-run" means the lead owns it, not that the lead executes it inline: the lead
  spawns one read-only subagent (the `Agent` tool, never a teammate) with the parent's
  acceptance criteria and the merged `$BASE_BRANCH`, carries on with the merge loop, and acts on
  the returned per-criterion result. It posts that result as a comment on the parent and runs
  `gh issue close <parent>` only when every criterion holds; otherwise the parent stays open, its
  label swaps from `agent-ready` to `needs-triage` (the comment is the record), and the gap is
  reported to the user.
- **mark in-progress** — `gh issue edit <N> --add-label "in-progress"`.
- **close on merge** — the teammate's PR body includes `Closes #<N>` (and `Closes #<M>` for
  every combined issue); GitHub auto-closes on merge. After merge, verify with
  `gh issue view <N> --json state --jq '.state'` == `CLOSED`.
- **branch / worktree** — branch `issue-<N>--<slug>`; worktree `worktree/issues/<N>--<slug>`.
  For a combined group, use the lowest issue number: `issue-<N>--<slug>`.

### Dependency and Sub-issue API

Paths are relative to `repos/{o}/{r}` (owner and repository). Every id these endpoints
take is the **numeric REST id** from `gh api repos/{o}/{r}/issues/{n} --jq .id`, NOT the
GraphQL node id that `gh issue view --json id` returns (that one 422s). Pass ids with typed
`-F`, not `-f`, so they are sent as integers.

Dependencies (ordering - what the marathon DAG consumes):

```bash
gh api repos/{o}/{r}/issues/{n}/dependencies/blocked_by                 # list blockers
gh api repos/{o}/{r}/issues/{n}/dependencies/blocking                   # reverse direction
BLOCKER_ID=$(gh api repos/{o}/{r}/issues/{m} --jq '.id')                # numeric REST id, NOT node id
gh api repos/{o}/{r}/issues/{n}/dependencies/blocked_by \
  --method POST -F issue_id="$BLOCKER_ID"                               # issue n is blocked by m
gh api repos/{o}/{r}/issues/{n}/dependencies/blocked_by/{id} --method DELETE   # {id} = numeric REST id
```

Sub-issues (decomposition with rollup, distinct from ordering):

```bash
gh api repos/{o}/{r}/issues/{n}/sub_issues                              # list children
gh api repos/{o}/{r}/issues/{n}/sub_issues --method POST -F sub_issue_id={id}
gh api repos/{o}/{r}/issues/{n}/sub_issue --method DELETE -F sub_issue_id={id}   # singular path
gh api repos/{o}/{r}/issues/{n}/sub_issues/priority --method PATCH -F sub_issue_id={id} -F after_id={id}
```

Constraints: one parent per issue, about 8 nesting levels, about 100 children per parent;
the parent's `sub_issues_summary` field carries the rollup progress.

sub-issues roll up progress; only `blocked_by` edges order the marathon DAG.

PR-as-blocker: unsupported (HTTP 422, "Target issue may only be an issue", observed POSTing a merged PR's numeric REST id as a `blocked_by` blocker)

Consequence: an open-PR blocker cannot be a native edge. Record "issue N waits for PR M" as
sequencing in the run plan and keep N out of every wave until M merges.

### Staleness Check

Labels were curated at triage time; open PRs may have appeared since. At run start, after the
Entry Gate and before the marathon skill spawns anyone, list open PRs with their changed files:

```bash
gh pr list --state open --limit 100 --json number,title,headRefName,files | \
  jq '[.[] | {number, title, headRefName, files: [.files[].path]}]'
```

For each queued `agent-ready` issue, compare the files it targets (paths named in the body, or
located by a quick read of the code it describes) against those lists. Ignore PRs on this run's
own `issue-*` branches. For any issue an open PR touches, surface it in the plan with exactly one
of three outcomes instead of spawning blind:
- **Combine** - the PR is unmerged work on the same change. The run does not push to another
  author's branch: combine is a recommendation surfaced to the user in the plan, with the issue
  held out of every wave (as for Sequence) until the user folds it into that PR or answers.
- **Sequence after the PR** - the issue is still valid but must build on the PR's result: record
  "issue N waits for PR M" in the wave table and hold N out of every wave until M merges (the
  open-PR blocker rule above).
- **Kick back to `needs-triage`** - the PR changes the issue's scope materially (partial fix,
  obsoletes it, or reverses what it asks): remove `agent-ready`, add `needs-triage` with a comment
  naming the PR, and drop the issue from this run.

The outcomes appear in the Dependency Analysis plan the marathon skill reports before Wave 1.

### Run

Use the marathon skill with the GitHub Work-Source Adapter above and the Marathon
Configuration values. The skill builds the DAG from native `blocked_by` deps plus hot-file
combining, spawns one teammate per issue or combined group, drives each PR via
pr-review-merge, and smart-merges in waves. Combined-issue teammates put `Closes #N` for
every issue they resolve in the PR body. Parents of decomposed issues are verification units:
no teammate, no `Closes #<parent>`, closed by the lead-run QA pass once the last child merges.

## Orchestrator Flow

```
/issues [label-filter] → detect teams → check agent-ready count → route:
  ├─ agent-ready exist → MARATHON (entry gate → staleness check → marathon skill, GitHub adapter)
  └─ none exist        → TRIAGE (promote → research → overlap → size → dependencies) → report → STOP
```
