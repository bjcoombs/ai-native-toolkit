---
name: marathon
description: >
  Run a list of work units to completion with an Agent Team: derive a dependency DAG and
  hot-file map, spawn one ephemeral teammate per unit (or combined group), drive each PR
  through pr-review-merge, smart-merge in waves, recover from crashes, and run a
  retrospective. Source-agnostic — the caller supplies a work-source adapter. Invoked by
  the /tm and /issues commands. TRIGGER when a command needs autonomous multi-unit team
  orchestration to completion, or when the user asks to run a tag/issue queue to done with
  Agent Teams.
---

# Marathon Engine

Source-agnostic team orchestration. The caller supplies a **work-source adapter**; this
skill owns DAG analysis, hot-file combining, team lifecycle, waves, crash recovery, and
the retrospective. It uses the `pr-review-merge` skill for every PR.

## Work-Source Adapter Contract

The calling command MUST fill these four operations before invoking this skill:

| Operation | What it returns / does |
|-----------|------------------------|
| **enumerate** | A list of work units, each `{id, title, requirements, dependencies[], complexity}` |
| **mark in-progress** | Marks one unit started in the source of truth |
| **close on merge** | How a merged PR closes the unit (e.g. a label, a status set, or PR `Closes #N`) |
| **branch / worktree** | The branch name and `worktree/<...>` path convention for a unit |

The caller also passes Marathon Configuration values (base branch, required approvals,
bot-reviewer rules, CI patterns) read from the project's CLAUDE.md.

## Phase 0: Capability Detection

```bash
# Agent Teams
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
```

Set `$TEAMS_AVAILABLE` (`true` if result is `"1"`).

### Project-Specific Configuration

Read the repo's CLAUDE.md for a `## Marathon Configuration` section. This provides project-specific overrides for marathon behavior. Extract these values (with defaults if section is missing):

| Setting | Default | Description |
|---------|---------|-------------|
| `$BASE_BRANCH` | `main` | Branch to create worktrees from and merge PRs into |
| `$REQUIRED_APPROVALS` | 1 | Minimum approvals for auto-merge |
| `$MARKDOWN_APPROVALS` | 1 | Approvals for markdown-only PRs |
| `$RETRO_LOG` | (none) | Path to retrospective log file |
| Bot reviewer rules | (none) | Per-bot thread resolution patterns |
| CI patterns | (none) | Known flaky checks, pre-existing failures |

If no Marathon Configuration section exists, **prompt the user to set one up before starting marathon mode**:
```
No Marathon Configuration found in this project's CLAUDE.md.

For best results, add a ## Marathon Configuration section to your project's CLAUDE.md.
Run `/tm-marathon-config-example` to see the template, then copy and customize it.

Proceeding with defaults: base branch=main, 1 approval, no bot reviewer rules.
```

Defaults apply for non-marathon use (single task mode, planning mode) without prompting. The template below uses `$BASE_BRANCH` where previous versions hardcoded `develop`.

## Step 1: DAG + Hot-File Analysis

**CRITICAL: Global Source-of-Truth Write Rule**
Never run source-of-truth write commands as parallel background jobs — concurrent writes race. Each such command may internally switch global state, and concurrent invocations can silently land work on wrong targets. Always run source-of-truth write commands **sequentially inline**. This was validated in the 047-security-audit marathon where 10 background `add-task` calls created tasks on wrong tags.

Enumerate work units via the adapter's **enumerate** operation.

**Analyze dependency tree for maximum concurrency:**

1. Map the dependency tree — which tasks block which?
2. Identify the critical path (longest sequential chain)
3. **Challenge unnecessary dependencies** — different files/modules may not need sequencing
4. Look for tasks chained sequentially that could run in parallel
5. **Identify hot files** — files touched by multiple tasks. Record as `$HOT_FILES`.
6. **Primary mitigation: combine tasks that share hot files** into one teammate. This eliminates merge conflicts entirely (proven across 3 marathons: 0 conflicts when combining). Combine when:
   - Tasks share hot files (strongest signal — always prefer combining over dependency management)
   - Tightly coupled output (e.g., "add resources" + "add docs for resources")
   - Content-only tasks touching non-overlapping directories (e.g., adding 3 independent pattern dirs)
   - One is docs/config for the other, or one is meaningless without the other
   - Small tasks (complexity 1-2) that share a theme — PR-per-task overhead exceeds the work itself
7. **Fallback: dependencies** — when combining isn't feasible (both tasks complexity 8+, fundamentally different concerns despite shared file, or 5+ tasks on one file):
   - **Add explicit dependencies** — merge the simpler/faster task first, then the other depends on it.
   - Teammate prompts: include conflict resolution patterns
   - If 5+ tasks touch one file and combining is unwieldy, consider a dedicated consolidation task
8. Report the optimized plan:
   ```
   ## Dependency Analysis: <tag>

   Critical path: <task-ids> (<N> points sequential)
   Parallel capacity: <M> tasks in first wave

   Combined tasks:
   - Tasks <X>+<Y>: <reason> (single teammate, single PR)

   Hot files:
   - <file-path>: tasks <ids> (pattern: <e.g., "accept both sides">)

   Optimizations:
   - Removed dependency <X> → <Y>: different modules
   ```
9. Apply dependency changes via the work source's dependency-update mechanism.

## Step 2: Team + Tracking

```
TeamCreate(
  team_name: "<tag>",
  description: "Marathon mode for tag <tag> - <N> tasks total, <M> ready"
)
```

**PR tracking** — persisted in worktree dir (survives crashes and team cleanup):

```bash
TRACK_FILE=~/dev/github.com/<org>/<repo>/worktree/<tag>/pr-tracking.json
mkdir -p "$(dirname "$TRACK_FILE")"

if [ -f "$TRACK_FILE" ]; then
  echo "EXISTING_TRACKING: reconciling against TM and GitHub"
else
  echo '{"meta":{"tag":"<tag>","wave":1,"repo":"<owner>/<repo>","flaky_checks":[]},"tasks":{}}' | jq . > "$TRACK_FILE"
fi
```

**Reconciliation** (run at start if tracking file exists):
1. Read unit status via the adapter's enumerate operation.
2. Cross-reference each tracking entry:
   - Source `done` but tracking `working` → merged externally. Remove from tracking.
   - Source `in-progress` but PR merged on GitHub → mark unit done, remove from tracking.
   - Source `pending` but tracking has PR → stale entry. Remove, check cleanup needed.
   - Source `in-progress` and PR open → valid. Keep, update `last_ci`.
3. Source `in-progress` but NOT in tracking → check GitHub for open PR. If found, add to tracking. If not, reset unit to `pending`.
4. Write reconciled file.

**Tracking structure:**
```json
{
  "meta": {"tag": "<tag>", "wave": 1, "repo": "<owner>/<repo>", "flaky_checks": ["E2E"]},
  "tasks": {
    "task-<id>": {
      "pr": 123,
      "status": "working|review_clear|merged",
      "model": "sonnet|opus",
      "wave": 1,
      "last_ci": "passing|failing|unstable|pending"
    }
  }
}
```

**CRUD operations:**
```bash
# Add/update task
jq --arg task "task-<id>" --argjson pr <number> --arg model "sonnet" --argjson wave 1 \
  '.tasks[$task] = {"pr": $pr, "status": "working", "model": $model, "wave": $wave, "last_ci": "pending"}' \
  "$TRACK_FILE" > "$TRACK_FILE.tmp" && mv "$TRACK_FILE.tmp" "$TRACK_FILE"

# Update CI status
jq --arg task "task-<id>" --arg ci "passing" \
  '.tasks[$task].last_ci = $ci' "$TRACK_FILE" > "$TRACK_FILE.tmp" && mv "$TRACK_FILE.tmp" "$TRACK_FILE"

# Read all
jq -r '.tasks | to_entries[] | "\(.key) → PR #\(.value.pr) (\(.value.status), CI: \(.value.last_ci), wave \(.value.wave))"' "$TRACK_FILE"

# Remove after merge+cleanup
jq --arg task "task-<id>" '.tasks |= del(.[$task])' "$TRACK_FILE" > "$TRACK_FILE.tmp" \
  && mv "$TRACK_FILE.tmp" "$TRACK_FILE"
```

**Identify known-flaky checks at marathon start:**
```bash
gh api repos/<owner>/<repo>/branches/$BASE_BRANCH/protection \
  --jq '.required_status_checks.contexts // []'
```
Store non-required check names in `meta.flaky_checks`.

## Step 3: Spawn Teammates

**Pre-spawn: Check for already-completed work:**
Before spawning Wave 1, check recent merged PRs for task keywords to avoid spawning work that's already done:
```bash
gh pr list --state merged --limit 20 --json title,mergedAt,headRefName \
  | jq '.[] | select(.headRefName | test("<tag>")) | {title, mergedAt, headRefName}'
```
Cross-reference with pending tasks. If a task's work was already merged (e.g., from a prior crashed marathon), mark it done and skip spawning.

**Model selection:**
- **Opus** (default for reliability): Multi-file PRs, review-heavy tasks, tasks touching shared files (barrel exports, routing, config), complexity 5+
- **Sonnet** (cost-efficient for simple work): Single-file changes, isolated modules, complexity 1-4 with no shared-file risk, docs/config-only tasks

Sonnet is cost-effective but has a recurring false REVIEW_CLEAR problem — reports review-clear without verifying all criteria (~15 min waste per marathon when it happens). Opus has 0 false signals across all marathons. When in doubt, use opus — the cost delta is cheaper than intervention time.

Haiku cannot reliably handle review loops — never use for teammates.

**Teammate prompt template:**
```
Task(
  subagent_type: "general-purpose",
  team_name: "<tag>",
  name: "task-<task-id>",
  model: "<chosen-model>",
  prompt: """
# Implement <tag>.<task-id>: <task-title>

## Setup
Set the unit in-progress via the adapter; create the worktree using the adapter's branch/worktree convention.

## Requirements
<task-description-and-subtasks from task-master show>

## Architectural Direction
<Include architectural guidance, design decisions, or constraints from the lead HERE in the
first message. Teammates may lose context between messages.>

## Project Guidelines
<Include relevant sections from the repo's CLAUDE.md - testing patterns, coding standards.>

## Shell Rules
Always pipe `gh` output to `jq` (never use `gh --jq` with complex filters). Use positive jq filters (`== "FAILURE"` not `!= "SUCCESS"`) - zsh mangles `!=`.

## Known Conflict Patterns
<If $HOT_FILES identified, include here. Otherwise omit.>
Additive files (imports, barrel exports, routes): accept both sides. Same-line conflicts or complex JSX blocks: escalate immediately with file, line range, and both versions.

## Workflow
1. **Implement using TDD**. Push commits incrementally for backup.
2. **Before creating PR**, check for existing: `gh pr list --head "<branch-name>" --state all --json number,state,mergedAt`
   - Merged → message lead, wait idle. Open → use it. None → create one.
3. **Review loop:** Use the pr-review-merge skill to drive your PR to green (5 criteria, thread rules, background CI watcher, batch fixes). Do not block on CI yourself.

## Communication
Only message the lead for **meaningful events**:
- PR created: `SendMessage(type: "message", recipient: "lead", content: "PR_CREATED: PR #<number> for <tag>.<task-id>", summary: "PR created <task-id>")`
- Review clear: `SendMessage(type: "message", recipient: "lead", content: "REVIEW_CLEAR: PR #<number> for <tag>.<task-id> — all criteria met", summary: "Review clear <task-id>")`
- Blocked: `SendMessage(type: "message", recipient: "lead", content: "BLOCKED: <tag>.<task-id> — <reason>", summary: "Blocked <task-id>")`
- Too complex: `SendMessage(type: "message", recipient: "lead", content: "TOO_COMPLEX: <tag>.<task-id> — <brief reasoning>", summary: "Too complex <task-id>")`

## Scope
- Only create PRs on YOUR branch (`<tag>--<task-id>--<slug>`). Never create PRs on other branches or for work outside your assigned task.
- If you discover related work that needs doing, mention it in your PR description — don't create additional PRs.

## Lifecycle
1. Implement → push incrementally → create PR → review loop until green
2. Message lead REVIEW_CLEAR and wait idle
3. Lead merges directly (no handshake needed), cleans up, and shuts you down
4. Approve shutdown request when received
"""
)
```

Spawn all independent teammates in a single message (parallel Task calls).

## Step 4: Lead Monitoring

## Smart Merge

## Crash Recovery

## Completion + Retrospective

## Subagent Fallback (no teams)
