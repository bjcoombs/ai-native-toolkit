---
description: Task Master - plan, start, review, and close
argument-hint: [tag [task-id] | feature description] (optional - derives context from worktree if omitted)
---

# Task Master Orchestrator

**$ARGUMENTS**

> Thin orchestrator. Delegates to subagents for implementation and review loops.
>
> **Planning Mode** (`/tm use stripe as kyc provider`): When args don't match an existing tag, explores the codebase, writes a PRD, creates a tag, generates tasks, runs complexity analysis, and expands. Stops after planning — run `/tm <tag>` to start work.
>
> **Marathon Mode** (`/tm <tag>`): When only a tag is given (no task-id), automatically progress through all ready tasks.
> When Agent Teams are available, each task gets its own teammate with shared task list.
> When teams unavailable, falls back to parallel subagents.
> PRs auto-merge when CI green, no conflicts, required approvals met, and 0 changes requested.

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

**Tag match check** (use tasks.json directly — CLI output has formatting that breaks grep):
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

**If `$PRD_EXISTS`**: Fast path — PRD already written, go straight to task generation.
**If not**: Slow path — explore codebase and write the PRD first.

### Fast Path (PRD exists)

#### Step 1: Read PRD and Estimate Task Count

```bash
TAG_NAME="$FIRST_ARG"
# $PRD_FILE set during Phase 1 detection
```

Read the PRD. Estimate how many top-level tasks it should produce, optimizing for **maximum concurrency** — prefer many independent tasks over fewer sequential ones. Consider:
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
1. Challenge sequential dependencies — are they genuinely blocking or just ordered by convention?
2. Identify tasks that could run in parallel but are chained
3. Apply fixes:
   ```bash
   task-master update-task --id=<task-id> --prompt="Remove dependency on task <X>, these are independent"
   ```
4. Report the optimized plan (see [Report and Stop](#report-and-stop))

### Slow Path (New idea, no PRD)

**Input**: `$ARGUMENTS` — natural language description (e.g., "use stripe as another kyc provider")

#### Step 1: Explore the Codebase

Use Glob, Grep, Read — or spawn an Explore agent for deeper investigation. Focus on:
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

**Step 3: Implement** — See [Implement Mode](#mode-implement-or-create_pr) below.

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

#### Ready Criteria (ALL must be true)

1. **Branch in sync** — no merge conflicts with `$BASE_BRANCH`
2. **CI passing** — ALL checks succeed, including coverage status checks (codecov, coveralls, etc.) — not just build and test
3. **All inline comments addressed** — see thread resolution rules
4. **No unaddressed conversation comments**
5. **All review threads resolved**

**Thread resolution rules:**
Follow bot reviewer rules from the project's CLAUDE.md Marathon Configuration. Generic defaults:
- **Bot reviewers**: Fix code, push. Resolve bot threads via GraphQL if addressed. Use jq JSON builder to avoid zsh `$` escaping:
  ```bash
  jq -n --arg tid "$THREAD_ID" '{"query": "mutation { resolveReviewThread(input: {threadId: \"\($tid)\"}) { thread { isResolved } } }"}' | gh api graphql --input -
  ```
- **Human**: Fix code, reply inline, @mention reviewer. Do NOT resolve — let them confirm.

#### Each Iteration

**1. Sync with `$BASE_BRANCH` (FIRST):**
```bash
git fetch origin $BASE_BRANCH && git merge origin/$BASE_BRANCH --no-edit
```

**2. Check criteria:**
```bash
# CI
gh pr checks <number> --json state | jq '.[] | select(.state == "FAILURE" or .state == "CANCELLED")' | head -1

# Unresolved threads (with path/line for local code checking)
gh api graphql -f query='query { repository(owner: "<owner>", name: "<repo>") {
  pullRequest(number: <number>) { reviewThreads(first: 50) { nodes {
    id isResolved path line comments(first: 1) { nodes { author { login } body } }
  }}}}}' --jq '.data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | {id, author: .comments.nodes[0].author.login, path, line, body: .comments.nodes[0].body[0:200]}'
# For each unresolved thread: check local code at path:line.
# If already fixed locally → resolve bot threads via GraphQL directly (skip push→re-review cycle).

# Conversation
gh pr view <number> --comments
```

**3. Decision tree:**
- Merge conflicts → Resolve or report blocked
- CI failing → Fix first
- Unresolved bot threads → Check local code first. If addressed, resolve via GraphQL. If not, fix and push.
- Unresolved human threads → Fix code, reply inline, @mention reviewer
- ALL clear → Output `<promise>PR_READY</promise>`

#### Review Loop

```
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: """
# Review PR #<number> for <tag>.<task-id>

Working directory: <worktree-path>

Each iteration: merge origin/$BASE_BRANCH first. Loop until ALL green:
1. No merge conflicts  2. CI passing  3. No unresolved inline comments
4. No unaddressed conversation comments  5. All YOUR review threads resolved

Fix issues, push, then spawn a background agent to watch CI:
  Agent(run_in_background: true, prompt: "Run gh pr checks <number> --watch --fail-fast, then check threads and comments. Return report.")
Stay responsive to messages while CI runs.
Report: ready, waiting, or blocked.
"""
)
```

---

### Mode: Implement or Create PR

```
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: "Implement <tag>.<task-id>: <task-title> in <worktree-path>. Requirements: <task-description-and-subtasks>. TDD, push, create PR, review loop (5 criteria: no conflicts, CI green, no unresolved threads/comments), background CI watcher. Report: ready, waiting, or blocked."
)
```

---

## Marathon Mode: Agent Teams

**Prerequisite:** `$MARATHON_MODE` AND `$TEAMS_AVAILABLE`.

**CRITICAL: Task Master Global Tag State Rule**
Never run `task-master add-task`, `set-status`, or `update-task` as parallel background jobs. Each command internally switches the global tag, and concurrent invocations race — tasks silently land on wrong tags. Always run TM write commands **sequentially inline**. This was validated in the 047-security-audit marathon where 10 background `add-task` calls created tasks on wrong tags.

### Step 1: Identify Tag and Ready Tasks

```bash
TAG=$(echo "$ARGUMENTS" | xargs)
cd ~/dev/github.com/<org>/<repo>
task-master tags use "$TAG" && task-master list --json
# Use jq on tasks.json for reliable status filtering (task-master next can suggest subtask IDs of done parents)
jq '."<tag>".tasks[] | {id, title: .title[0:50], status, dependencies, complexity}' .taskmaster/tasks/tasks.json
```

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
7. **Fallback: TM dependencies** — when combining isn't feasible (both tasks complexity 8+, fundamentally different concerns despite shared file, or 5+ tasks on one file):
   - **Add explicit TM dependencies** — merge the simpler/faster task first, then the other depends on it.
     ```bash
     task-master add-dependency --id=<later-task> --depends-on=<earlier-task>
     ```
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
9. Apply dependency changes:
   ```bash
   task-master update-task --id=<task-id> --prompt="Remove dependency on task <X>, these are independent"
   ```

### Step 2: Create Team and Initialize Tracking

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
1. Read TM status: `task-master tags use "<tag>" && task-master list --json`
2. Cross-reference each tracking entry:
   - TM `done` but tracking `working` → merged externally. Remove from tracking.
   - TM `in-progress` but PR merged on GitHub → mark TM done, remove from tracking.
   - TM `pending` but tracking has PR → stale entry. Remove, check cleanup needed.
   - TM `in-progress` and PR open → valid. Keep, update `last_ci`.
3. TM `in-progress` but NOT in tracking → check GitHub for open PR. If found, add to tracking. If not, reset TM to `pending`.
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

### Step 3: Spawn Teammates

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
Create worktree from $BASE_BRANCH, set task in-progress, cd to worktree. Standard TM worktree pattern: `worktree/<tag>/<task-id>--<slug>`.

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
3. **Review loop** - all 5 criteria must be green:
   No merge conflicts | ALL CI checks passing (including coverage gates) | No unresolved inline comments | No unaddressed conversation comments | All review threads resolved
4. **After pushing, spawn background CI watcher** (never block on CI yourself):
   `Agent(run_in_background: true, prompt: "Run gh pr checks <number> --watch --fail-fast, then check threads/comments/merge state. Return structured report.")`
   Stay responsive to lead messages while CI runs.
5. **When background agent reports**: fix issues, batch fixes into fewer pushes, spawn new watcher after each push. Dismiss stale bot CHANGES_REQUESTED reviews after each push. Contradictory bot comments - resolve yourself, don't escalate. Merge conflicts - resolve using conflict patterns, only escalate if genuinely ambiguous.
6. **Before reporting REVIEW_CLEAR**, verify ALL criteria via API: `mergeStateStatus` CLEAN/UNSTABLE, 0 pending checks, 0 unresolved threads. This is a hard gate - the lead trusts this signal to merge without re-verifying.

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

### Step 4: Lead Monitoring

Report team status after spawning:
```
## Marathon Started: <tag>

| Task | Teammate | Model | Status |
|------|----------|-------|--------|
| <task-id> - <title> | task-<task-id> | sonnet | Spawned |
```

#### Teammate Messages (reactive)

| Message | Lead Action |
|---------|-------------|
| PR_CREATED | Update tracking, report to user |
| REVIEW_CLEAR | Run [Smart Merge](#smart-merge) — teammate is idle, merge directly |
| CLARIFICATION_NEEDED | Answer from task context, or relay to user if genuinely ambiguous |
| BLOCKED (merge conflicts) | Push back: "Resolve conflicts yourself" |
| BLOCKED (genuine) | Report to user, ask for guidance |
| TOO_COMPLEX | Shutdown teammate, decompose task, spawn fresh |

**On TOO_COMPLEX:**
1. Shutdown teammate, clean up failed worktree/branch
2. Decompose: `task-master expand --id=<task-id> --research` (subtasks) or cancel + create new peer tasks (sibling split)
3. Spawn fresh teammates for resulting tasks

**Lead conflict resolution**: When a teammate is idle and their PR is DIRTY (merge conflict), resolve it directly instead of nudging the teammate. Pull `$BASE_BRANCH`, resolve the conflict, push. Faster than round-tripping to an idle teammate (~10 min saved per conflict).

**Non-responsive teammate escalation**: If a teammate ignores a direct instruction (nudge to fix CI, address review feedback, follow lead guidance) or sends a false REVIEW_CLEAR (claims ready but criteria aren't met), don't keep nudging. Shut it down and respawn the same task on opus. One strike — don't give sonnet a second chance on the same task.

**Idle teammate ≠ dead teammate**: Before killing an idle teammate, check if their worktree has subagent activity. Subagents run as child processes and cause idle notifications on the parent. Check for recent file modifications or running processes in the worktree before assuming the teammate is stalled:
```bash
# Check for recent activity in teammate's worktree (files modified in last 5 min)
find ~/dev/github.com/<org>/<repo>/worktree/<tag>/<task-id>--<slug> -mmin -5 -type f | head -3
```

**Unreliable REVIEW_CLEAR**: Don't rely solely on messages. **Proactively poll ALL tracked PRs** on two triggers:
1. When any teammate goes idle or sends a message
2. **Periodically** — every ~30 minutes during long marathons to catch stalled teammates early

```bash
for PR in $(jq -r '.tasks | to_entries[] | select(.value.status == "working") | .value.pr' "$TRACK_FILE"); do
  THREADS=$(gh api graphql -f query="query { repository(owner: \"<owner>\", name: \"<repo>\") {
    pullRequest(number: $PR) { reviewThreads(first: 100) { nodes { isResolved } } }
  }}" | jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length')
  MERGE=$(gh pr view $PR --json mergeStateStatus,mergedAt,statusCheckRollup \
    | jq '{mergeStateStatus, mergedAt, checks: [.statusCheckRollup[] | select(.conclusion == "FAILURE" or .conclusion == "CANCELLED")] | length}')
  echo "PR #$PR: threads=$THREADS merge=$MERGE"
done
```
If green with 0 unresolved threads, run smart-merge regardless of teammate message. If stalled (teammate idle but PR not green), message teammate to continue.

**Lead overlap rule:** Never block on a single PR's CI. While waiting for CI on one PR, process other actionable items: teammate shutdowns, tracking updates, next-wave setup, cleanup of merged PRs, spawning newly unblocked tasks. The lead loop is event-driven, not sequential.

**Accidental input guard:** Empty messages, single characters, or auto-suggested prompt text → brief status summary only, no expensive operations.

#### Smart Merge

Triggered by REVIEW_CLEAR, idle teammate with green PR, or human typing "check".

**Known: Stale bot CHANGES_REQUESTED.** Some bot reviewers submit CR reviews that GitHub does not auto-dismiss on re-review. Check the project's Marathon Configuration for bot-specific patterns. Default: dismiss any stale bot CHANGES_REQUESTED before merging.

```bash
PR=<number>

# Step 1: Dismiss stale bot CRs
STALE_REVIEWS=$(gh api repos/<owner>/<repo>/pulls/$PR/reviews \
  --jq '[.[] | select(.state == "CHANGES_REQUESTED" and (.user.login | endswith("[bot]")))]')
echo "$STALE_REVIEWS" | jq -r '.[].id' | while read REVIEW_ID; do
  gh api repos/<owner>/<repo>/pulls/$PR/reviews/$REVIEW_ID/dismissals \
    --method PUT -f message="Stale bot review — verified findings addressed" -f event="DISMISS"
done

# Step 2: Check merge state
gh pr view $PR --json mergeStateStatus,mergedAt,reviews \
  | jq '{
    mergeStateStatus,
    mergedAt,
    approvals: [.reviews[] | select(.state == "APPROVED")] | length,
    changesRequested: [.reviews[] | select(.state == "CHANGES_REQUESTED")] | length
  }'
```

**Auto-Merge Criteria (ALL must be true):**
1. `mergeStateStatus` is `"CLEAN"` — OR `"UNSTABLE"` with only non-required checks failing
2. At least `$REQUIRED_APPROVALS` approvals — OR `$MARKDOWN_APPROVALS` for markdown-only PRs (some bot reviewers skip them)
3. Zero non-dismissed changes-requested reviews
4. **Base branch is healthy** — if the PR's CI failures exist on `$BASE_BRANCH` too (pre-existing), do NOT merge and compound the problem. Instead, spawn a separate worktree/PR to fix the failing tests on `$BASE_BRANCH` first, then rebase and merge the original PR.

**UNSTABLE handling:** If `mergeStateStatus == "UNSTABLE"`, check failing checks against `meta.flaky_checks` and any CI patterns from the project's Marathon Configuration. If ALL failing checks are non-required AND not pre-existing on `$BASE_BRANCH`, treat as merge-eligible. Report: "Merging with UNSTABLE — only non-required checks failing: <names>". If failures ARE pre-existing on `$BASE_BRANCH`, fix it first (criterion 4).

**UNKNOWN handling:** GitHub sometimes returns `mergeStateStatus: "UNKNOWN"` even when all checks pass. If UNKNOWN but CI all green and 0 unresolved threads, retry up to 3 times with 30s backoff. If still UNKNOWN after retries, treat as CLEAN and proceed (log the override).

**Verify before merging** — never trust teammate claims:
```bash
gh pr view $PR --json state,mergedAt,mergeStateStatus | jq '{state, mergedAt, mergeStateStatus}'
```
Trust the API, not the message.

**Merge directly when teammate is idle.** After REVIEW_CLEAR, teammates go idle automatically. No handshake needed — just verify the PR via API and merge. The lead overlap rule means you're already processing other work while teammates finish.

**After merge:**
```bash
cd ~/dev/github.com/<org>/<repo>
task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=done
cd <repo>-main && git worktree remove --force ../worktree/<tag>/<task-id>--<slug>
git branch -D <tag>--<task-id>--<slug>
```

Then:
1. Report to user
2. Shutdown teammate
3. Mark internal task completed
4. Check for newly unblocked tasks
5. **Wave transition**: Batch-dismiss stale CRs across all eligible PRs before spawning next wave. Review signals from completed wave, adapt next prompts with learnings.
6. Check ready tasks with `task-master list --json` filtered to `pending` status (**not** `task-master next` — `next` can suggest subtask IDs of done parents). Spawn fresh teammates for ready tasks.
7. If all done → [Completion](#step-6-completion-and-retrospective)

**If not merge-ready:**
- BLOCKED → report to user, message teammate
- DIRTY → message teammate: "resolve merge conflicts"
- Human changes requested → report to user

#### Merge Order (multiple PRs)

1. **Batch-dismiss stale CRs** across ALL eligible PRs first:
   ```bash
   for PR in <all-eligible-pr-numbers>; do
     gh api repos/<owner>/<repo>/pulls/$PR/reviews \
       --jq '.[] | select(.state == "CHANGES_REQUESTED" and (.user.login | endswith("[bot]"))) | .id' \
     | while read REVIEW_ID; do
       gh api repos/<owner>/<repo>/pulls/$PR/reviews/$REVIEW_ID/dismissals \
         --method PUT -f message="Stale bot review — batch dismissed" -f event="DISMISS"
     done
   done
   ```
2. **Sort by hot-file impact**: Fewer hot files first. Hot-file PRs merge LAST.
3. **Merge sequentially**: One at a time. 10-20s between for conflict detection.
4. **Re-check merge state** after each — CLEAN can flip to DIRTY from cascade.

### Ephemeral Teammates

One task, one session. Task Master is the coordination brain.

```
Spawn → Setup worktree → Implement → Create PR → Review loop → REVIEW_CLEAR → Idle
  → Lead: smart-merge + cleanup → Shutdown teammate
```

Never reuse a teammate for a different task. Shutdown + spawn fresh.

### Lead Authority

The lead operates as a **tech lead running a sprint** — not a task router.

**The lead delegates, never executes.** During a marathon, the lead's job is to coordinate: merge PRs, spawn teammates, monitor status. Any work that takes more than ~30 seconds (tests, coverage, code exploration, implementation, thread resolution) must be delegated to a teammate or subagent. The lead must stay responsive to teammate messages at all times. The moment the lead starts executing, messages queue up and momentum stalls.

**Trusted decisions (no human approval needed):**
- Defer or cancel tasks that become irrelevant
- Combine related tasks into one PR
- Create new/follow-up Task Master tasks
- Fix minor style nits across PRs directly
- Escalate model tier (shutdown + respawn on opus)
- Limit concurrency to prevent merge conflicts
- Reprioritize based on learnings

**Always escalate to human:**
- Architectural changes not in original task descriptions
- Public API surface modifications
- Deferring more than 30% of tasks
- Ambiguous reviewer feedback

**Report judgment calls in status updates.**

### Crash Recovery

If the session crashes, teammates linger as "active members" preventing `TeamDelete()`.

```bash
# 1. Force-remove stale team
rm -rf ~/.claude/teams/<tag>
rm -rf ~/.claude/tasks/<tag>

# 2. Check worktrees for uncommitted work
git worktree list | grep "<tag>"

# 3. Resume: /tm <tag>
# Reconciliation handles stale tracking entries automatically.
```

Worktrees and `pr-tracking.json` survive crashes in `worktree/<tag>/`.

### Step 6: Completion and Retrospective

1. Send `shutdown_request` to each remaining teammate
2. Wait for all shutdown approvals
3. Call `TeamDelete()` - if it fails with "active members", verify panes are dead and retry. **TeamDelete is mandatory** - without it, teamContext persists and blocks future team creation in this session.
4. **PRD delivery check** — re-read the original PRD/issue and its success criteria. Cross-reference against merged PRs. Report:
   - Criteria met (with PR evidence)
   - Criteria not met or partially met (flag for user)
   - Scope that was delivered beyond the original PRD (emergent work)
4. Read the retro log (path from Marathon Configuration `$RETRO_LOG`, or skip if not configured)
5. Run retrospective using the structured format below
6. Append this marathon to the retro log (Marathon History + update Template Changes validation)

```
## Marathon Complete: <tag>

All <N> tasks done. <N> PRs merged.

<PR table: Wave | PR | Tasks | Title | Merged time>

### PRD Delivery

| Criterion | Status | Evidence |
|-----------|--------|----------|
| <success criterion from PRD> | Met / Partial / Not met | PR #<N>, ... |

<If any not met, explain what's remaining and whether follow-up tasks are needed.>

### Retrospective

**Template changes tested this run**
<Check marathon-retros.md Template Changes table for "Pending" items.
For each that was exercised: did it help, hurt, or not apply? Mark validated.>

**What worked well**
- <specific patterns, tools, or approaches — with evidence>

**What didn't work — with waste estimate**
- <friction point>: ~<N> min lost. Root cause: <X>
- <friction point>: ~<N> min lost. Root cause: <X>

**Lead decisions log**
- <decision>: <alternatives considered> -> <chosen> because <reason>
(task combinations, dependency changes, merge ordering, model upgrades, interventions)

**Where does this learning belong?** (specific command / cross-cutting rule / README / don't capture)
- <target file or section>: <concrete change with rationale>

**What clause is no longer earning its place?**
- <existing rule or block>: <reason it can be removed or demoted>

**Stats**
- Tasks: <N> completed, <N> cancelled/deferred
- PRs: <N> merged, <N> avg review iterations
- Teammates: <N> spawned (<N> sonnet, <N> opus), <N> needed intervention
- Wall clock: ~<N> min spawn to final merge
- Estimated waste: ~<N> min (CI waits, conflict resolution, stalled teammates)
```

---

## Marathon Mode: Subagent Fallback

When `$MARATHON_MODE` but `$TEAMS_AVAILABLE` is `false`, use parallel subagents after each cleanup cycle (see Post-Cleanup in Cleanup mode).

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

