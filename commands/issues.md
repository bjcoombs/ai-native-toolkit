---
description: GitHub-issue marathon - triage open issues, then run agent-ready ones to merge with Agent Teams
argument-hint: [scope-label] (optional - narrows which open issues are considered; default: all open issues)
---

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

For each issue, assess whether it is actionable as-is (clear scope, acceptance criteria
inferable, no open question):
- **Clear enough** → add the agent-ready label:
  `gh issue edit <N> --add-label "agent-ready"`
- **Ambiguous** → post clarifying questions as a comment, then label needs-triage:
  ```bash
  gh issue comment <N> --body "$(cat <<'EOF'
  Triage questions before this can be picked up by an agent:
  1. <question>
  2. <question>
  EOF
  )"
  gh issue edit <N> --add-label "needs-triage"
  ```

Then **report and STOP** (mirrors `/tm` planning):
```
## Issue Triage: <org>/<repo>

Tagged agent-ready: #12, #15, #18
Tagged needs-triage (questions posted): #20, #21

OK to start on the agent-ready issues? Re-run /issues to begin, or reply to proceed.
```

Do NOT spawn teammates in triage mode.

## Marathon Mode (agent-ready issues exist)

### GitHub Work-Source Adapter

Supply the marathon skill's adapter as:
- **enumerate** — `gh issue list --label "agent-ready" "${FILTER[@]}" --state open --json number,title,body,labels`
  (scoped by the optional `$ARGUMENTS` label filter);
  dependencies from `gh api repos/$ORG/$REPO/issues/<N>/dependencies/blocked_by`
  (each blocker issue number is a dependency edge). Complexity: infer from issue body/labels.
- **mark in-progress** — `gh issue edit <N> --add-label "in-progress"`.
- **close on merge** — the teammate's PR body includes `Closes #<N>` (and `Closes #<M>` for
  every combined issue); GitHub auto-closes on merge. After merge, verify with
  `gh issue view <N> --json state --jq '.state'` == `CLOSED`.
- **branch / worktree** — branch `issue-<N>--<slug>`; worktree `worktree/issues/<N>--<slug>`.
  For a combined group, use the lowest issue number: `issue-<N>--<slug>`.

### Run

Use the marathon skill with the GitHub Work-Source Adapter above and the Marathon
Configuration values. The skill builds the DAG from native `blocked_by` deps plus hot-file
combining, spawns one teammate per issue or combined group, drives each PR via
pr-review-merge, and smart-merges in waves. Combined-issue teammates put `Closes #N` for
every issue they resolve in the PR body.

## Orchestrator Flow

```
/issues [label-filter] → detect teams → check agent-ready count → route:
  ├─ agent-ready exist → MARATHON (marathon skill, GitHub adapter)
  └─ none exist        → TRIAGE (tag agent-ready / post questions+needs-triage) → report → STOP
```
