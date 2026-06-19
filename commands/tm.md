---
description: Task Master - plan, start, review, and close
argument-hint: [tag [task-id] | feature description] (optional - derives context from worktree if omitted)
---

# Task Master Orchestrator

**$ARGUMENTS**

> Thin orchestrator. Delegates to subagents for implementation and review loops.
>
> **Planning Mode** (`/tm use stripe as kyc provider`): When args don't match an existing tag, explores the codebase, writes a PRD, creates a tag, generates tasks, runs complexity analysis, and expands. Stops after planning - run `/tm <tag>` to start work.
>
> **Marathon Mode** (`/tm <tag>`): When only a tag is given (no task-id), automatically progress through all ready tasks.
> When Agent Teams are available, each task gets its own teammate with shared task list.
> When teams unavailable, falls back to parallel subagents.
> PRs auto-merge when CI green, no conflicts, required approvals met, and 0 changes requested.

---

## TM Work-Source Adapter

This command supplies the marathon skill's adapter as:
- **enumerate** — `task-master tags use "<tag>" && task-master list --json`; use `jq` on `tasks.json` for reliable status filtering (`task-master next` can suggest subtask IDs of done parents).
- **mark in-progress** — `task-master set-status --id=<id> --status=in-progress` (run sequentially inline — never as a parallel background job; concurrent TM writes race the global tag).
- **close on merge** — `task-master tags use "<tag>" && task-master set-status --id=<id> --status=done`.
- **branch / worktree** — branch `<tag>--<task-id>--<slug>`; worktree `worktree/<tag>/<task-id>--<slug>`.

---

## Phase 0: Detect Capabilities

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

---

## Phase 1: Detect Context and Mode

**Parse arguments to determine mode:**
- No arguments → Report mode
- One argument that matches an existing TM tag → Marathon mode (`$MARATHON_MODE = true`)
- Two arguments (tag + task-id) → Single task mode
- **Arguments that don't match any existing tag** → Planning mode (`$PLANNING_MODE = true`)

**Tag match check** (use tasks.json directly - CLI output has formatting that breaks grep):
```bash
cd ~/dev/github.com/<org>/<repo>
FIRST_ARG="<first-argument>"
jq -e --arg tag "$FIRST_ARG" '.[$tag]' .taskmaster/tasks/tasks.json >/dev/null 2>&1 && echo "TAG_EXISTS" || echo "NEW_IDEA"
```

**If NEW_IDEA, search for existing PRD in the repo:**
```bash
cd ~/dev/github.com/<org>/<repo>/<repo>-main
# Search common PRD locations and filename patterns
fd -t f "$FIRST_ARG" --extension md . | head -5
# Also check conventional path
ls .taskmaster/prd/$FIRST_ARG.md 2>/dev/null
```
- File found → `$PLANNING_MODE = true`, `$PRD_EXISTS = true`, `$PRD_FILE = <path>`
- No match → `$PLANNING_MODE = true`, `$PRD_EXISTS = false`

**Early routes:**
- If `$PLANNING_MODE`: Jump to [Planning Mode](#planning-mode)
- If `$MARATHON_MODE` AND `$TEAMS_AVAILABLE`: Jump to [Marathon Mode: Agent Teams](#marathon-mode-agent-teams)

**Check context in order:**
1. Current directory - pwd matches TM worktree pattern?
2. Conversation context - Recent "📍 Current Work" footer with worktree path?
3. Arguments - Explicit tag/task-id provided?

### Detect Worktree Pattern

```bash
pwd | grep -q '/worktree/[^/]*/[^/]*--' && echo "IN_WORKTREE" || echo "NOT_IN_WORKTREE"
```

**If IN_WORKTREE**: Extract tag and task-id from path, proceed with PR check.
**If NOT_IN_WORKTREE but conversation shows active work**: cd to the worktree.

**Decision tree:**

```
Is current directory a TM worktree?
(Pattern: worktree/<tag>/<task-id>--<slug>)
│
├─ YES → Check PR state
│   ├─ PR merged → CLEANUP mode
│   ├─ PR open   → REVIEW mode
│   └─ No PR     → IMPLEMENT mode
│
└─ NO → START mode
    ├─ Args match existing tag → Marathon or single task
    ├─ Args don't match any tag → PLANNING mode
    └─ No args → Report ready tasks
```

**CRITICAL: Never auto-start new tasks.** Conversation context can continue existing work, but starting a NEW task requires explicit `$ARGUMENTS`.

### Check PR State

```bash
gh pr view --json number,state,mergedAt 2>/dev/null
```

| Condition | Action |
|-----------|--------|
| `mergedAt` is set | CLEANUP mode |
| PR exists, open | REVIEW mode |
| No PR | IMPLEMENT mode |

---

## Planning Mode

**If `$PRD_EXISTS`**: Fast path - PRD already written, go straight to task generation.
**If not**: Slow path - explore codebase and write the PRD first.

### Fast Path (PRD exists)

#### Step 1: Read PRD and Estimate Task Count

```bash
TAG_NAME="$FIRST_ARG"
# $PRD_FILE set during Phase 1 detection
```

Read the PRD. Estimate how many top-level tasks it should produce, optimizing for **maximum concurrency** - prefer many independent tasks over fewer sequential ones. Consider:
- Each distinct module/component/endpoint = separate task
- Shared infrastructure (types, config, migrations) = early task others depend on
- Tests that can run independently = separate tasks
- Documentation = separate task (parallelizes with everything)

#### Step 2: Parse PRD as New Tag

```bash
cd ~/dev/github.com/<org>/<repo>
task-master tags add "$TAG_NAME"
task-master tags use "$TAG_NAME"
task-master parse-prd --input="$PRD_FILE" --num-tasks=<estimated-count>
```

#### Step 3: Complexity Analysis

```bash
task-master analyze-complexity --research
```

#### Step 4: Expand into Subtasks

```bash
task-master tags use "$TAG_NAME" && task-master list --json
```

Expand tasks with complexity >= 5 that are **not already done**:
```bash
# NEVER expand done tasks — creates phantom subtasks that are busywork to close.
# If a parent is merged, create new peer tasks instead.
for TASK_ID in <high-complexity-pending-task-ids>; do
  task-master expand --id=$TASK_ID --research
done
```

#### Step 5: Validate Dependency Graph

Review the generated dependency tree and optimize for concurrency:
1. Challenge sequential dependencies - are they genuinely blocking or just ordered by convention?
2. Identify tasks that could run in parallel but are chained
3. Apply fixes:
   ```bash
   task-master update-task --id=<task-id> --prompt="Remove dependency on task <X>, these are independent"
   ```
4. Report the optimized plan (see [Report and Stop](#report-and-stop))

### Slow Path (New idea, no PRD)

**Input**: `$ARGUMENTS` - natural language description (e.g., "use stripe as another kyc provider")

#### Step 1: Explore the Codebase

Use Glob, Grep, Read - or spawn an Explore agent for deeper investigation. Focus on:
- Existing patterns the feature should follow
- Files and modules that would be touched
- Integration points and dependencies

#### Step 2: Write the PRD

```bash
TAG_NAME="<derived-tag-name>"  # lowercase, hyphens, max 40 chars
mkdir -p ~/dev/github.com/<org>/<repo>/.taskmaster/prd
```

PRD structure:
```markdown
# <Feature Title>

## Problem Statement
## Technical Context
## Solution
## Scope
## Success Criteria
## Complexity Estimate
```

#### Step 3-5: Same as Fast Path

Create tag, parse PRD, complexity analysis, expand, validate dependencies.

### Report and Stop

```
## Planned: <tag-name>

PRD: .taskmaster/prd/<tag-name>.md

| Task | Title | Points | Subtasks | Deps |
|------|-------|--------|----------|------|

**Total**: <N> points across <M> tasks
**Critical path**: <task-ids> (<P> points sequential)
**Parallel capacity**: <K> tasks can run concurrently in first wave

Next: `/clear` then `/tm <tag-name>`
```

**Do NOT start implementation.** Planning mode produces the plan only.

---

## Start Mode (Not in worktree)

### No Arguments → Report Only

```bash
task-master list --all-tags --ready --json
```

Report ready tasks and **terminate**. Do NOT launch subagent.

### With Arguments → Setup then Implement

**Step 1: Setup**
```bash
cd ~/dev/github.com/<org>/<repo>/<repo>-main
git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
git branch <tag>--<task-id>--<slug>
mkdir -p ../worktree/<tag>
git worktree add ../worktree/<tag>/<task-id>--<slug> <tag>--<task-id>--<slug>
cd ~/dev/github.com/<org>/<repo>
task-master tags use "<tag>" && task-master set-status --id="<task-id>" --status=in-progress
cd ~/dev/github.com/<org>/<repo>/worktree/<tag>/<task-id>--<slug>
```

**Step 2: Get task details**
```bash
task-master show <task-id> --json
```

**Step 3: Implement** - See [Implement Mode](#mode-implement-or-create-pr) below.

---

## Worktree Modes

Derive context from path:
```bash
TAG=$(basename $(dirname "$(pwd)"))
TASK_DIR=$(basename "$(pwd)")
TASK_ID="${TASK_DIR%%--*}"
```

### Mode: Cleanup

cd to repo root first (subagent will delete the worktree).

```
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: "PR merged for <tag>.<task-id>. From ~/dev/github.com/<org>/<repo>: mark task done, remove worktree, delete branch."
)
```

**Post-Cleanup (Marathon, no teams):** Check for next ready tasks:
```bash
task-master tags use "<tag>" && task-master list --ready --json
```
- No ready → Report tag complete, terminate
- 1 ready → Start that task
- Multiple independent → Spawn parallel agents

---

### Mode: Review (PR open)

Use the pr-review-merge skill to drive PR #<number> to merge-ready (5 criteria, thread
rules, background CI watcher). When all criteria are met, output `<promise>PR_READY</promise>`.

---

### Mode: Implement or Create PR

```
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: "Implement <tag>.<task-id>: <task-title> in <worktree-path>. Requirements: <task-description-and-subtasks>. TDD, push, create PR, use the pr-review-merge skill for the review loop. Report: ready, waiting, or blocked."
)
```

---

## Marathon Mode: Agent Teams

Prerequisite: `$MARATHON_MODE` AND `$TEAMS_AVAILABLE`.

Use the marathon skill, supplying the TM Work-Source Adapter above and the Marathon
Configuration values from Phase 0. The skill owns DAG/hot-file analysis, team lifecycle,
waves, crash recovery, and the retrospective; it drives each PR via pr-review-merge.

---

## Marathon Mode: Subagent Fallback

When `$MARATHON_MODE` but `$TEAMS_AVAILABLE` is `false`, the marathon skill's Subagent
Fallback runs parallel subagents after each cleanup cycle using the TM adapter.

---

## Orchestrator Flow

```
/tm [args...] → detect capabilities → parse args → route:
  │
  ├─ Args don't match tag → PLANNING (explore → PRD → tasks → expand → stop)
  │
  ├─ Marathon + Teams → AGENT TEAMS (spawn teammates, smart-merge, waves)
  │
  ├─ No PR → IMPLEMENT (subagent)
  ├─ PR open → REVIEW (subagent)
  ├─ PR merged → CLEANUP → (marathon? check next ready)
  │
  └─ No context → report ready tasks
```

