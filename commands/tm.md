---
description: Task Master - unified command for start, review, and close
argument-hint: [tag [task-id]] (optional - derives context from worktree if omitted)
---

# Task Master Orchestrator

**$ARGUMENTS**

> Thin orchestrator. Uses Ralph Loop for iteration when available, falls back to subagents.
>
> **Marathon Mode** (`/tm <tag>`): When only a tag is given (no task-id), automatically progress through all ready tasks.
> When Agent Teams are available, each task gets its own teammate with shared task list.
> When teams unavailable, falls back to parallel subagents.
> YOU review and merge PRs at your own pace - marathon mode handles the mechanical workflow.

---

## Phase 0: Detect Ralph Plugin

```bash
ls ~/.claude/plugins/cache/claude-plugins-official/ralph-loop/*/commands/ralph-loop.md 2>/dev/null && echo "RALPH_AVAILABLE" || echo "RALPH_NOT_AVAILABLE"
```

Set `$RALPH_AVAILABLE` based on result. If Ralph not available, warn once:
> ⚠️ Ralph Loop plugin not installed. Using subagents (may burn context on long test runs). Install: `/plugin install ralph-loop`

### Detect Agent Teams

```bash
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
```

Set `$TEAMS_AVAILABLE` to `true` if result is `"1"`, otherwise `false`.

---

## Phase 1: Detect Context and Mode

**Parse arguments to determine mode:**
- No arguments → Report mode
- One argument (non-numeric, matches a TM tag) → Marathon mode (`$MARATHON_MODE = true`)
- Two arguments (tag + task-id) → Single task mode

**Marathon + Agent Teams early route:**
If `$MARATHON_MODE` AND `$TEAMS_AVAILABLE`: Skip normal single-task flow. Jump directly to [Marathon Mode: Agent Teams](#marathon-mode-agent-teams).

**Check context in order:**
1. Current directory - pwd matches TM worktree pattern?
2. Conversation context - Recent "📍 Current Work" footer with worktree path?
3. Arguments - Explicit tag/task-id provided?

### Detect Worktree Pattern

```bash
pwd
# Check if pwd contains worktree pattern (git command may fail in repo root)
pwd | grep -q '/worktree/[^/]*/[^/]*--' && echo "IN_WORKTREE" || echo "NOT_IN_WORKTREE"
```

**If IN_WORKTREE**: Extract tag and task-id from path, proceed with PR check.

**If NOT_IN_WORKTREE but conversation shows active work:**
```bash
cd ~/dev/github.com/<org>/<repo>/worktree/<tag>/<task-id>--<slug>
```

**Decision tree:**

```
Is current directory a TM worktree (or cd'd to one via conversation context)?
(Pattern: worktree/<tag>/<task-id>--<slug>)
│
├─ YES → Check PR state (quick gh commands only)
│   ├─ PR merged → Launch cleanup-specialist (subagent, always)
│   ├─ PR open   → Ralph: review loop / Fallback: review-specialist
│   └─ No PR     → Ralph: full cycle / Fallback: implement-specialist
│
└─ NO → START mode
    ├─ Args given → Setup worktree, then Ralph: full cycle / Fallback: start-specialist
    └─ No args    → Report ready tasks (no subagent needed)
```

**CRITICAL: Never auto-start new tasks.** Conversation context can continue existing work (review, cleanup), but starting a NEW task requires explicit `$ARGUMENTS`.

### Check PR State (Quick - No Heavy Processing)

```bash
# Quick state check only - subagent does the real work
gh pr view --json number,state,mergedAt 2>/dev/null
```

| Condition | Action (Ralph) | Action (Fallback) |
|-----------|----------------|-------------------|
| `mergedAt` is set | cleanup-specialist | cleanup-specialist |
| PR exists, open | Ralph review loop | review-specialist |
| No PR | Ralph full cycle | implement-specialist |

---

## START MODE (Not in worktree)

### No Arguments → Report Only (No Subagent)

```bash
task-master list --all-tags --ready --json
```

Report and **terminate**:
```
## Ready Tasks

| Tag | Task | Title | Points |
|-----|------|-------|--------|
| <tag> | <id> | <title> | <points> |

Run `/tm <tag> <task-id>` to start a task.
```

**Do NOT launch subagent.** User chooses, then re-runs `/tm` with args.

### With Arguments → Setup then Implement

Extract tag and task-id from arguments.

**Step 1: Setup (quick, no subagent needed)**

```bash
cd ~/dev/github.com/<org>/<repo>/<repo>-main
git checkout develop && git pull origin develop
# Use -- separator (not /) to avoid git ref conflicts when tag branch exists
git branch <tag>--<task-id>--<slug>
mkdir -p ../worktree/<tag>
git worktree add ../worktree/<tag>/<task-id>--<slug> <tag>--<task-id>--<slug>
# Run task-master from repo root (where .taskmaster/ lives), not worktree
cd ~/dev/github.com/<org>/<repo>
task-master tags use "<tag>" && task-master set-status --id="<task-id>" --status=in-progress
# Then cd to worktree for implementation
cd ~/dev/github.com/<org>/<repo>/worktree/<tag>/<task-id>--<slug>
```

**Step 2: Get task details**

```bash
task-master show <task-id> --json
```

**Step 3: Implement (Ralph or Fallback)**

See [Full Cycle: Ralph](#full-cycle-ralph) or [Full Cycle: Fallback](#full-cycle-fallback) below.

---

## WORKTREE MODES

Derive context from path:
```bash
TAG=$(basename $(dirname "$(pwd)"))
TASK_DIR=$(basename "$(pwd)")
TASK_ID="${TASK_DIR%%--*}"
```

---

### Mode: CLEANUP → Launch cleanup-specialist

**CRITICAL: cd to repo root BEFORE launching subagent.** The subagent will delete the worktree, and if parent's cwd is invalid when it completes, stop hooks fail.

**Step 1: cd to repo root first**
```bash
cd ~/dev/github.com/<org>/<repo> && pwd
```

**Step 2: Launch cleanup-specialist**
```
Task(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: """
# Cleanup <tag>.<task-id>

PR merged. Clean up in this order:
1. Mark task done: `cd ~/dev/github.com/<org>/<repo> && task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=done`
2. Remove worktree from <repo>-main: `cd <repo>-main && git worktree remove ../worktree/<tag>/<task-id>--<slug>`
3. Delete branch: `git branch -d <tag>--<task-id>--<slug>`

Report complete.
"""
)
```

**Step 3: Post-Cleanup - Check Next Ready (Marathon Mode)**

If `$MARATHON_MODE` is `true` AND `$TEAMS_AVAILABLE` is `false` (subagent fallback), check for next ready tasks and continue:

```bash
# Get next ready tasks in the tag
task-master tags use "<tag>" && task-master list --ready --json
```

**Decision tree:**
- **No ready tasks** → Report tag complete, terminate
- **1 ready task** → Start that task (loop back to START mode setup)
- **Multiple ready tasks** → Check dependencies:
  - If independent (no shared dependencies) → Spawn parallel agents for each
  - If sequential → Start first task only

**For parallel execution:**
```
# Spawn multiple agents in parallel (using multiple Task calls in single message)
Task(subagent_type: "general-purpose", ...) # Task A
Task(subagent_type: "general-purpose", ...) # Task B
Task(subagent_type: "general-purpose", ...) # Task C
```

Each parallel agent runs the full `/tm <tag> <task-id>` workflow independently.

---

### Mode: REVIEW (PR open)

#### Ready Criteria (ALL must be true before PR_READY)

**"Green" means ALL of these are true:**
1. **Branch in sync** - No merge conflicts with develop
2. **CI passing** - All checks succeed (or skipped)
3. **All inline comments addressed** - See resolution rules below
4. **No unaddressed conversation comments** - Actionable feedback responded to
5. **All review threads resolved** - No unresolved threads remain

**Inline comment resolution rules:**
- **Bot threads** (claude[bot], coderabbitai[bot]): Fix the code and push. Bots re-review and resolve their own threads
- **Human threads**: Fix the code, **reply inline** explaining the fix, `@mention` the reviewer to resolve. Do NOT resolve human threads — let the reviewer confirm

#### Sync with develop (EVERY iteration, do this FIRST)

```bash
# Merge develop to prevent drift and catch conflicts early
git fetch origin develop && git merge origin/develop --no-edit
# If conflicts: resolve them, commit, push
# If can't auto-resolve: report blocked
```

#### Check remaining criteria

```bash
# 2. CI must be passing
gh pr checks <number> --json state --jq '.[] | select(.state != "SUCCESS" and .state != "SKIPPED")' | head -1

# 3 & 5. Check ALL unresolved review threads (bots resolve their own on re-review)
gh api graphql -f query='query { repository(owner: "<owner>", name: "<repo>") {
  pullRequest(number: <number>) { reviewThreads(first: 50) { nodes {
    id isResolved comments(first: 1) { nodes { author { login } body } }
  }}}}}' --jq '.data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | {id, author: .comments.nodes[0].author.login, body: .comments.nodes[0].body[0:100]}'
# If unresolved threads remain:
#   Bot author → code fix not pushed yet, or bot hasn't re-reviewed (wait/push)
#   Human author → reply inline explaining fix, @mention them to resolve

# 4. Check conversation for unaddressed comments
gh pr view <number> --comments
```

**Decision tree:**
- Merge conflicts → Resolve or report blocked
- CI failing → Fix CI first
- Unresolved bot threads → Fix code, push, wait for bot re-review
- Unresolved human threads → Fix code, reply inline, @mention reviewer
- Actionable conversation comments → Respond or fix
- ALL clear → Output `<promise>PR_READY</promise>`

#### Review Loop

**If `$RALPH_AVAILABLE`:** Invoke Ralph for review loop.

**CRITICAL**: The Ralph args are passed to bash UNQUOTED. Shell special chars like `(`, `)`, `&`, `;`, `#` will cause parse errors or silent truncation. NEVER include task titles or descriptions in the args - reference tasks by ID only.

```
Skill(
  skill: "ralph-loop:ralph-loop",
  args: "Review PR <number> for <tag>.<task-id> in <worktree-path>. FIRST merge origin/develop to stay in sync. Then loop until ALL green -- no merge conflicts, CI passing, no unresolved inline comments, conversation addressed, your review threads resolved. Fix issues, push, wait 60s, check again. --max-iterations 10 --completion-promise PR_READY --tag <tag> --task <task-id>"
)
```

**Note**: Output `<promise>PR_READY</promise>` ONLY when ALL five criteria are met.

**If Ralph NOT available:** Launch review-specialist subagent:

```
Task(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: """
# Review PR #<number> for <tag>.<task-id>

Working directory: <worktree-path>

Each iteration: merge origin/develop first to stay in sync.

Loop until ALL green:
1. No merge conflicts with develop
2. CI checks passing
3. No unresolved inline comments from bots or reviewers
4. No unaddressed conversation comments
5. All YOUR review threads resolved

Fix issues, push, wait for CI, repeat. Spawn opus for complex code changes.

Report: ready (all 5 criteria met), waiting (CI running), or blocked (need human input).
"""
)
```

---

### Mode: IMPLEMENT or CREATE_PR (no PR exists)

**If `$RALPH_AVAILABLE`:** Invoke Ralph for full cycle.

**CRITICAL**: The Ralph args are passed to bash UNQUOTED. Shell special chars like `(`, `)`, `&`, `;`, `#` will cause parse errors or silent truncation. NEVER include task titles or descriptions in the args - reference tasks by ID only.

```
Skill(
  skill: "ralph-loop:ralph-loop",
  args: "Complete <tag>.<task-id> in <worktree-path>. Run task-master show <task-id> for requirements. TDD -- test, fix, commit. Push, create PR, then loop until ALL green. Each iteration merge origin/develop first, then check -- no merge conflicts, CI passing, no unresolved inline comments, conversation addressed, your review threads resolved. --max-iterations 20 --completion-promise PR_READY --tag <tag> --task <task-id>"
)
```

**Note**: Output `<promise>PR_READY</promise>` ONLY when ALL five criteria are met.

**If Ralph NOT available:** Launch implement-specialist subagent:

```
Task(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: """
# Implement <tag>.<task-id>: <task-title>

Working directory: <worktree-path>

You're an orchestrator. Spawn opus for complex code changes.

## Requirements
<task-description-and-subtasks>

## Flow
1. Implement using TDD (run tests, fix failures, commit)
2. Push and create PR
3. Monitor CI, fix issues
4. Report: PR ready, waiting, or blocked

⚠️ Long test runs may burn context. Consider installing Ralph plugin: `/plugin install ralph-loop`
"""
)
```

---

## Marathon Mode: Agent Teams

**Prerequisite:** `$MARATHON_MODE` is `true` AND `$TEAMS_AVAILABLE` is `true`.

This mode uses Claude Code Agent Teams to give each task its own teammate session, with a shared task list for coordination.

### Step 1: Identify Tag and Ready Tasks

Extract tag from `$ARGUMENTS`:
```bash
TAG=$(echo "$ARGUMENTS" | xargs)
```

Get all tasks for the tag with dependencies:
```bash
cd ~/dev/github.com/<org>/<repo>
task-master tags use "$TAG" && task-master list --json
jq '."<tag>".tasks[] | {id, title: .title[0:50], status, dependencies, complexity}' .taskmaster/tasks/tasks.json
```

**Analyze dependency tree for maximum concurrency:**

Before spawning, review the dependency graph and optimize:
1. Map the dependency tree — which tasks block which?
2. Identify the critical path (longest sequential chain)
3. **Challenge unnecessary dependencies** — if task B depends on task A but they touch different files/modules, the dependency may be overly cautious. Remove it.
4. Look for tasks chained sequentially that could run in parallel (e.g., "add API endpoint" → "add tests" could be parallel if tests can be written against the interface spec)
5. Report the optimized plan to the user:
   ```
   ## Dependency Analysis: <tag>

   Critical path: <task-ids> (<N> points sequential)
   Parallel capacity: <M> tasks can run concurrently in first wave

   Optimizations applied:
   - Removed dependency <X> → <Y>: different modules, no actual coupling
   - ...
   ```
6. Apply any dependency changes:
   ```bash
   task-master update-task --id=<task-id> --prompt="Remove dependency on task <X>, these are independent"
   ```

If no ready tasks, report tag status and terminate.

### Step 2: Create Team

```
TeamCreate(
  team_name: "<tag>",
  description: "Marathon mode for tag <tag> - <N> tasks total, <M> ready"
)
```

### Step 3: Create Internal Tasks and Spawn Teammates

For each ready, independent task:

**Create internal task:**
```
TaskCreate(
  subject: "Implement <tag>.<task-id>: <task-title>",
  description: "<full task details from task-master show>",
  activeForm: "Implementing <tag>.<task-id>"
)
```

**Spawn teammate (choose model based on task complexity):**
- Simple tasks (docs, config, complexity 1-3): `model: "haiku"` or `model: "sonnet"`
- Moderate tasks (complexity 4-6): `model: "sonnet"`
- Complex tasks (refactoring, architecture, complexity 7+): `model: "opus"`

Use judgment. A one-line version bump doesn't need Opus. A multi-file refactor does.

```
Task(
  subagent_type: "general-purpose",
  team_name: "<tag>",
  name: "task-<task-id>",
  model: "<chosen-model>",
  prompt: """
# Implement <tag>.<task-id>: <task-title>

## Setup
```bash
cd ~/dev/github.com/<org>/<repo>/<repo>-main
git checkout develop && git pull origin develop
git branch <tag>--<task-id>--<slug>
mkdir -p ../worktree/<tag>
git worktree add ../worktree/<tag>/<task-id>--<slug> <tag>--<task-id>--<slug>
cd ~/dev/github.com/<org>/<repo>
task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=in-progress
cd ~/dev/github.com/<org>/<repo>/worktree/<tag>/<task-id>--<slug>
```

## Requirements
<task-description-and-subtasks from task-master show>

## Workflow
1. **Implement using TDD**: Write failing tests, make them pass, refactor. Commit incrementally.
2. **Push and create draft PR early** (on first meaningful commit): `gh pr create --draft --title "<type>: <title>" --body "..."`. This gives the human visibility into progress. Continue pushing commits to the draft PR.
3. **When implementation is complete**, mark ready for review: `gh pr ready <number>`. Set TM task status to review: `task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=review`
4. **Review loop**: Merge origin/develop first each iteration. Then check all 5 green criteria:
   - No merge conflicts with develop — **resolve conflicts yourself**, don't report blocked
   - CI passing
   - No unresolved inline comments (bots resolve their own; reply to humans)
   - No unaddressed conversation comments
   - All your review threads resolved
5. Fix any issues, push, wait 60s, check again. Loop until all green.
   - **Merge conflicts are routine** — `git fetch origin develop && git merge origin/develop`, resolve conflicts, commit, push. Only report blocked if the conflict is genuinely ambiguous (e.g., two competing architectural approaches).
6. **When PR is green**: Message the lead.

## Communication
- When draft PR is created: `SendMessage(type: "message", recipient: "lead", content: "Draft PR #<number> created for <tag>.<task-id>", summary: "Draft PR for <task-id>")`
- When PR marked ready for review: `SendMessage(type: "message", recipient: "lead", content: "PR #<number> marked ready for review for <tag>.<task-id>", summary: "PR ready for review <task-id>")`
- When PR is green (all checks passing): `SendMessage(type: "message", recipient: "lead", content: "PR #<number> all checks green for <tag>.<task-id>", summary: "PR green <task-id>")`
- If blocked: `SendMessage(type: "message", recipient: "lead", content: "Blocked on <reason>", summary: "Blocked on <task-id>")`
- If task is too complex to complete in one session: `SendMessage(type: "message", recipient: "lead", content: "Task <task-id> is too complex for a single session. Suggest splitting: <brief reasoning>", summary: "Too complex, suggest split <task-id>")`
  - Don't struggle silently — if you're going in circles or the scope is clearly larger than one PR, flag it early

## Lifecycle
1. Implement, create draft PR early, mark ready when complete, review loop until green → message lead "PR green"
2. Wait idle until lead messages you that the PR has been merged
3. On "merged" message from lead, run cleanup:
   ```bash
   cd ~/dev/github.com/<org>/<repo>
   task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=done
   cd <repo>-main && git worktree remove ../worktree/<tag>/<task-id>--<slug>
   git branch -d <tag>--<task-id>--<slug>
   ```
4. Message lead confirming cleanup complete
5. Approve shutdown request from lead (or self-terminate)
"""
)
```

Spawn all independent teammates in a single message (parallel Task calls).

### Step 4: Lead Monitoring

After spawning teammates, report team status:
```
## Marathon Started: <tag>

| Task | Teammate | Status |
|------|----------|--------|
| <task-id> - <title> | task-<task-id> | Spawned |
| ... | ... | ... |

Waiting for teammates to create PRs. I'll report when PRs are ready for your review.
```

**Lead behavior — two concurrent concerns:**

The lead handles two concerns: reacting to teammate messages, and merge/conflict/comment detection via a dedicated watcher.

#### Loop A: Teammate Messages (reactive)

- On teammate message "Draft PR created": Acknowledge, update tracking, add PR to watcher list (respawn watcher if needed)
- On teammate message "PR ready for review": Acknowledge, report to user: "PR #X for <tag>.<task-id> is ready for your review"
- On teammate message "PR green": Acknowledge, report to user: "PR #X for <tag>.<task-id> — all checks passing, ready to merge"
- On teammate message "Blocked":
  - **Merge conflicts** → Push back: message teammate "Resolve the merge conflicts yourself — fetch develop, merge, fix conflicts, commit, push. Only escalate if the conflict involves ambiguous architectural decisions."
  - **Genuinely blocked** (missing API, unclear requirements, needs human decision) → Report to user, ask for guidance
- On teammate message "Too complex" / "Task too large":
  1. Shutdown the teammate (their session is now stale with failed attempts)
  2. Clean up the failed worktree/branch if one was created
  3. Assess the teammate's reasoning and decide how to decompose:
     - **Subtasks** (task is one logical unit but needs phased delivery): `task-master expand --id=<task-id> --research`
     - **Sibling split** (task is actually two+ independent concerns): Cancel the original task and create new peer tasks:
       ```bash
       task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=cancelled
       task-master add-task --title="<first half>" --description="..." --priority=<p>
       task-master add-task --title="<second half>" --description="..." --priority=<p>
       ```
     Use judgment based on what the teammate reported.
  4. Spawn fresh teammates for the resulting tasks
  5. Report to user: "Task <task-id> was too complex. Split into N tasks, spawning teammates."
- When multiple PRs ready, consolidate: "N PRs ready for your review: #X, #Y, #Z"

#### Loop B: Merge Detection (watcher teammate)

The lead spawns a dedicated **watcher** teammate (Haiku, minimal cost) that polls for merges and messages the lead when it detects one. This solves the turn-based limitation — the watcher's messages wake the lead.

**Spawn watcher when there are PRs to track.** The watcher is ephemeral — killed and respawned whenever the PR list changes.

```
Task(
  subagent_type: "general-purpose",
  team_name: "<tag>",
  name: "watcher",
  model: "haiku",
  prompt: """
# PR Watcher

You are a lightweight polling agent. Check tracked PRs for state changes and notify the lead.

## Tracked PRs
- PR #<number> → task <task-id> → teammate: task-<task-id>
- PR #<number> → task <task-id> → teammate: task-<task-id>

## Loop
Repeat until told to stop:
1. Check all tracked PRs:
   ```bash
   for PR in <pr-numbers>; do
     gh pr view $PR --json number,mergedAt,mergeable --jq '{number, mergedAt, mergeable}'
   done
   ```
2. **Merged** — if any PR has `mergedAt` set, message the lead:
   `SendMessage(type: "message", recipient: "lead", content: "MERGED: PR #<number> (task <task-id>)", summary: "PR #<number> merged")`
3. **Merge conflicts** — if `mergeable` is `"CONFLICTING"`, message the lead:
   `SendMessage(type: "message", recipient: "lead", content: "CONFLICT: PR #<number> (task <task-id>) has merge conflicts with base branch", summary: "PR #<number> conflicts")`
4. Check for new review activity:
   ```bash
   for PR in <pr-numbers>; do
     gh api repos/<owner>/<repo>/pulls/$PR/comments \
       --jq '[.[] | select(.created_at > "<last-check-timestamp>")] | length'
   done
   ```
5. **New comments** — if count > 0, message the lead:
   `SendMessage(type: "message", recipient: "lead", content: "COMMENTS: PR #<number> (task <task-id>) has <N> new review comments since last check", summary: "PR #<number> new comments")`
6. Wait 90 seconds: `sleep 90`
7. Repeat from step 1. Update `<last-check-timestamp>` to current time each iteration.

When the lead sends a shutdown request, approve it immediately.
Do NOT do anything else. No code changes, no analysis. Just poll and notify.
"""
)
```

**When the lead receives a notification from watcher:**

**MERGED** notification:
1. **Shutdown the watcher** — PR list is stale
2. Report to user: "Detected PR #X merged. Triggering cleanup for <tag>.<task-id>..."
3. **Message the task teammate**: `SendMessage(type: "message", recipient: "task-<task-id>", content: "PR #X merged. Run cleanup: mark TM task done, remove worktree, delete branch. Then confirm.", summary: "PR merged, run cleanup")`
4. Wait for teammate's cleanup confirmation, then **shutdown the task teammate**
5. Mark internal task completed via TaskUpdate
6. Check for newly unblocked tasks:
   ```bash
   task-master tags use "<tag>" && task-master list --ready --json
   ```
7. Spawn fresh task teammates for any newly ready tasks
8. **Respawn a fresh watcher** with the updated PR list (remaining + any new PRs)
9. If all tasks done → Don't respawn watcher, proceed to [Step 6: Completion](#step-6-completion)

**CONFLICT** notification:
1. **Message the task teammate**: `SendMessage(type: "message", recipient: "task-<task-id>", content: "Your PR has merge conflicts with the base branch. Fetch develop, merge, resolve conflicts, commit and push.", summary: "Resolve merge conflicts")`
2. Watcher keeps polling — no need to kill/respawn for conflicts

**COMMENTS** notification:
1. **Message the task teammate**: `SendMessage(type: "message", recipient: "task-<task-id>", content: "Your PR has new review comments. Check inline and conversation comments, address them, push fixes.", summary: "New review comments on PR")`
2. Watcher keeps polling — no need to kill/respawn for comments

**Human can also type "check"** in the lead session to trigger an immediate merge check without waiting for the watcher.

### Ephemeral Teammates Principle

Teammates are **one task, one session**. Task Master is the coordination brain — all state lives there, not in session context.

**Lifecycle of a teammate:**
```
Spawn (fresh) → Setup worktree → Implement → Draft PR (early) → Ready for review → Review loop → PR green → Idle
  → Lead detects merge → Messages teammate → Teammate runs cleanup → Confirms → Shutdown
```

The teammate stays alive through the full cycle because it already has the context (paths, branch names, task IDs) needed for cleanup. But once cleanup is done, the session is terminated — never reused for a different task.

**Why shutdown instead of reuse:**
- Shutdown is instant. Compaction/clearing is slow and unreliable.
- No context bloat from prior task's code, test output, review comments.
- Task Master already knows what to do next — the new session just reads it.
- Each task gets full context budget for its own work.

**Never reuse a teammate for a different task.** Always shutdown + spawn fresh.

### Step 6: Completion

When all tasks in tag are done:
1. Shutdown any remaining teammates via `SendMessage(type: "shutdown_request", ...)` (most will already be shut down from the merge cycle)
2. `TeamDelete()` to clean up team resources
3. Report:
```
## Marathon Complete: <tag>

All <N> tasks done. <N> PRs merged.
```

---

## Marathon Mode: Subagent Fallback

When `$MARATHON_MODE` is `true` but `$TEAMS_AVAILABLE` is `false`, the existing parallel subagent approach is used (see [Post-Cleanup Marathon Continuation](#step-3-post-cleanup---check-next-ready-marathon-mode) in the cleanup section).

---

## Orchestrator Flow

```
/tm [tag [task-id]] → check Ralph → detect teams → parse args → route:
  │
  ├─ Marathon + Teams available → Agent Teams mode (dedicated section)
  │   ├─ Create team, spawn teammates for ready tasks
  │   ├─ Each teammate: setup worktree, implement, PR, review loop
  │   ├─ Lead dual loop:
  │   │   ├─ Loop A (reactive): teammate messages → track, report to human
  │   │   └─ Loop B (watcher): Haiku teammate polls PRs → detects merges/conflicts/comments → notifies lead
  │   └─ All done → shutdown team
  │
  ├─ No PR (implement/create)
  │   ├─ Ralph available → invoke Ralph (full cycle)
  │   └─ Ralph missing   → launch implement-specialist (subagent)
  │
  ├─ PR open (review)
  │   ├─ Ralph available → invoke Ralph (review loop)
  │   └─ Ralph missing   → launch review-specialist (subagent)
  │
  ├─ PR merged (cleanup) → launch cleanup-specialist (always subagent)
  │   │
  │   └─ Marathon mode (no teams)? Check next ready tasks:
  │       ├─ No ready → Report tag complete, STOP
  │       ├─ 1 ready → Start task, LOOP to top
  │       └─ N ready independent → Spawn N parallel agents, LOOP
  │
  └─ No context → list ready tasks
```

**Marathon Mode Behavior:**
- **With Agent Teams**: Teammates run as parallel sessions within the team, shared task list, direct messaging. Lead coordinates lifecycle.
- **Without Agent Teams (fallback)**: Parallel subagents after each cleanup cycle.
- Human merges PRs at their own pace (never auto-merge)
- After merge detected → cleanup → check next ready → auto-start
- Independent tasks spawn in parallel (multiple concurrent PRs)
- Loop continues until all tasks in tag are done

---

## Task Status Semantics

| Status      | Meaning                                   |
|-------------|-------------------------------------------|
| pending     | Not started                               |
| in-progress | Currently being worked on                 |
| review      | Implementation complete, PR pending merge |
| done        | PR merged, work verified and complete     |
| blocked     | Cannot proceed due to external dependency |
| deferred    | Postponed for later                       |
| cancelled   | Will not be done                          |

## Task Status Lifecycle

```
/tm starts (START)  → Task: pending → in-progress
Implementation      → Subtasks: pending → review (code complete)
All subtasks review → Parent: STAYS in-progress
PR created/polished → Parent: STAYS in-progress
Human merges PR     → (no command runs)
/tm after merge     → Parent: in-progress → done
```

---

## Notes

- Human merges PRs, never auto-merge (even in marathon mode)
- Subagents can bail if work is too large
- `/tm` → report ready tasks
- `/tm <tag>` → marathon mode (work through entire tag)
- `/tm <tag> <task-id>` → single task mode
- **Marathon mode**: Runs continuously until all tasks in tag are done
  - Example: `/tm saga-script-versioning` works through entire tag
  - You merge PRs when ready → system auto-continues
  - Parallelizes independent tasks automatically
