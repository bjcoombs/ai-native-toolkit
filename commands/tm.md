---
description: Task Master - plan, start, review, and close
argument-hint: [tag [task-id] | feature description] (optional - derives context from worktree if omitted)
---

# Task Master Orchestrator

**$ARGUMENTS**

> Thin orchestrator. Uses Ralph Loop for iteration when available, falls back to subagents.
>
> **Planning Mode** (`/tm use stripe as kyc provider`): When args don't match an existing tag, explores the codebase, writes a PRD, creates a tag, generates tasks, runs complexity analysis, and expands. Stops after planning — run `/tm <tag>` to start work.
>
> **Marathon Mode** (`/tm <tag>`): When only a tag is given (no task-id), automatically progress through all ready tasks.
> When Agent Teams are available, each task gets its own teammate with shared task list.
> When teams unavailable, falls back to parallel subagents.
> PRs auto-merge when CI green, no conflicts, ≥2 approvals, and 0 changes requested.

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
- One argument that matches an existing TM tag → Marathon mode (`$MARATHON_MODE = true`)
- Two arguments (tag + task-id) → Single task mode
- **Arguments that don't match any existing tag** → Planning mode (`$PLANNING_MODE = true`)

**Tag match check** (use tasks.json directly — CLI output has formatting that breaks grep):
```bash
cd ~/dev/github.com/<org>/<repo>
FIRST_ARG="<first-argument>"
jq -e --arg tag "$FIRST_ARG" '.[$tag]' .taskmaster/tasks/tasks.json >/dev/null 2>&1 && echo "TAG_EXISTS" || echo "NEW_IDEA"
```

**Planning mode early route:**
If `$PLANNING_MODE`: Skip normal flow. Jump directly to [Planning Mode](#planning-mode).

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
    ├─ Args match existing tag → Setup worktree, then Ralph: full cycle / Fallback: start-specialist
    ├─ Args don't match any tag → PLANNING mode (explore → PRD → tasks → expand)
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

## PLANNING MODE (args don't match any existing tag)

The user has described a feature idea. Transform it into a fully planned Task Master tag with tasks, complexity analysis, and subtask breakdown.

**Input**: `$ARGUMENTS` — a natural language description of the idea (e.g., "use stripe as another kyc provider")

### Step 1: Explore the Codebase

Understand the existing architecture relevant to the idea. Use Glob, Grep, Read — or spawn an Explore agent for deeper investigation.

Focus on:
- Existing patterns the feature should follow (interfaces, factories, adapters)
- Files and modules that would be touched
- Integration points and dependencies
- Feasibility and complexity signals

### Step 2: Write the PRD

Based on exploration findings, write a PRD to `.taskmaster/prd/`:

```bash
# Auto-derive tag name from the description (lowercase, hyphens, max 40 chars)
# e.g., "use stripe as another kyc provider" → "stripe-kyc-provider"
TAG_NAME="<derived-tag-name>"
```

PRD structure (keep it concise — this feeds into task generation):

```markdown
# <Feature Title>

## Problem Statement
<What problem does this solve? Why now?>

## Technical Context
<Relevant existing patterns, interfaces, modules discovered during exploration>
<File paths and line references>

## Solution
<What to build, following existing patterns>

## Scope
<What's in, what's explicitly out>

## Success Criteria
<How do we know it's done? Testable conditions>

## Complexity Estimate
<Initial story point estimate with reasoning>
```

```bash
# Write the PRD
mkdir -p ~/dev/github.com/<org>/<repo>/.taskmaster/prd
# Write PRD content to .taskmaster/prd/<tag-name>.md
```

### Step 3: Create Tag and Generate Tasks

```bash
cd ~/dev/github.com/<org>/<repo>
task-master tags add "$TAG_NAME"
task-master tags use "$TAG_NAME"
task-master parse-prd --input=".taskmaster/prd/$TAG_NAME.md"
```

### Step 4: Complexity Analysis

```bash
task-master analyze-complexity --research
```

### Step 5: Expand Tasks

Expand tasks that warrant subtasks (typically complexity >= 5):

```bash
# Get tasks needing expansion
task-master list --json | jq '.[] | select(.complexity >= 5) | .id'

# Expand each
for TASK_ID in <high-complexity-task-ids>; do
  task-master expand --id=$TASK_ID --research
done
```

### Step 6: Report and Stop

Report the complete task breakdown and **terminate**. User reviews, then runs `/tm <tag>` in a fresh session to start work.

```
## Planned: <tag-name>

PRD: .taskmaster/prd/<tag-name>.md

| Task | Title | Points | Subtasks | Deps |
|------|-------|--------|----------|------|
| 1 | ... | 3 | 2 | - |
| 2 | ... | 5 | 3 | 1 |
| ... | ... | ... | ... | ... |

**Total**: <N> points across <M> tasks
**Critical path**: <task-ids> (<P> points sequential)
**Parallel capacity**: <K> tasks can run concurrently in first wave

Next: `/clear` then `/tm <tag-name>`
```

**Do NOT start implementation.** Planning mode produces the plan only.

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
2. Remove worktree from <repo>-main: `cd <repo>-main && git worktree remove --force ../worktree/<tag>/<task-id>--<slug>`
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
- **CodeRabbit threads**: Fix the code and push. CodeRabbit re-reviews automatically and resolves its own threads. **NEVER reply in CodeRabbit threads** — CodeRabbit ignores replies from other bots ("Skipped: comment is from another GitHub bot"). Thread replies are wasted effort.
- **claude[bot] threads**: Resolve directly via GraphQL `resolveReviewThread` mutation if the concern is addressed in the current code
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
#   CodeRabbit author → fix code, push (triggers re-review). NEVER reply in thread.
#   claude[bot] author → resolve via resolveReviewThread if addressed
#   Human author → reply inline explaining fix, @mention them to resolve

# 4. Check conversation for unaddressed comments
gh pr view <number> --comments
```

**Decision tree:**
- Merge conflicts → Resolve or report blocked
- CI failing → Fix CI first
- Unresolved CodeRabbit threads → Fix code, push, wait for re-review (never reply in thread)
- Unresolved claude[bot] threads → Resolve via GraphQL if addressed
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
5. **Detect shared-file conflicts** — if two tasks would modify the same file (e.g., both touch `manifest.proto` or both modify the same migration directory), force sequential execution even if dependency analysis says they're independent. Merge conflicts from parallel edits to the same file are predictable and avoidable. Check task descriptions and titles for overlapping modules/files.
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
2. **Push commits incrementally** during implementation for backup. Do NOT create a PR yet.
3. **When implementation is complete**, create the PR and set TM status: `gh pr create --title "<type>: <title>" --body "..."` then `task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=review`. This ensures CodeRabbit and other bots review immediately.
4. **Review loop**: Merge origin/develop first each iteration. Then check all 5 green criteria:
   - No merge conflicts with develop — **resolve conflicts yourself**, don't report blocked
   - CI passing
   - No unresolved inline comments (fix code for CodeRabbit concerns — never reply in their threads; reply to humans)
   - No unaddressed conversation comments
   - All your review threads resolved
5. Fix any issues, push, wait 60s, check again. Loop until all green.
   - **Merge conflicts are routine** — `git fetch origin develop && git merge origin/develop`, resolve conflicts, commit, push. Only report blocked if the conflict is genuinely ambiguous (e.g., two competing architectural approaches).
6. **When review loop is green**, message lead "REVIEW_CLEAR" and wait idle. The lead handles merge checks and cleanup — do NOT attempt to merge or clean up yourself.

## Communication
Only message the lead for **meaningful events**. Do NOT message for status updates, idle state, or "still working" — the lead sees your idle notifications automatically.

- When PR is created: `SendMessage(type: "message", recipient: "lead", content: "PR_CREATED: PR #<number> for <tag>.<task-id>", summary: "PR created <task-id>")`
- When review loop is clear: `SendMessage(type: "message", recipient: "lead", content: "REVIEW_CLEAR: PR #<number> for <tag>.<task-id> — all review criteria met", summary: "Review clear <task-id>")`
- If blocked: `SendMessage(type: "message", recipient: "lead", content: "BLOCKED: <tag>.<task-id> — <reason>", summary: "Blocked <task-id>")`
- If task is too complex: `SendMessage(type: "message", recipient: "lead", content: "TOO_COMPLEX: <tag>.<task-id> — <brief reasoning>", summary: "Too complex <task-id>")`
  - Don't struggle silently — if you're going in circles or the scope is clearly larger than one PR, flag it early

## Lifecycle
1. Implement, push commits incrementally, create PR when complete, review loop until green
2. Message lead "REVIEW_CLEAR" and wait idle
3. Lead handles: smart-merge (dismiss stale bot reviews, check mergeStateStatus, merge), cleanup (mark TM done, remove worktree, delete branch), then shuts you down
4. Approve shutdown request from lead when received
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

**PR tracking**: Persist tracking to a file so it survives context compaction:
```bash
# File: ~/.claude/teams/<tag>/pr-tracking.json
# Structure: {"task-<id>": {"pr": <number>, "status": "open|merged|cleaning"}}

# Write/update tracking
TRACK_FILE=~/.claude/teams/<tag>/pr-tracking.json
jq --arg task "task-<id>" --argjson pr <number> \
  '.[$task] = {"pr": $pr, "status": "open"}' "$TRACK_FILE" > "$TRACK_FILE.tmp" \
  && mv "$TRACK_FILE.tmp" "$TRACK_FILE"

# Read all tracked PRs
jq -r 'to_entries[] | "\(.key) → PR #\(.value.pr) (\(.value.status))"' "$TRACK_FILE"

# Remove after merge+cleanup
jq --arg task "task-<id>" 'del(.[$task])' "$TRACK_FILE" > "$TRACK_FILE.tmp" \
  && mv "$TRACK_FILE.tmp" "$TRACK_FILE"
```
Update on PR_CREATED messages, remove after merge+cleanup. Use this list when human types "check" to smart-merge all tracked PRs.

**Context compaction recovery**: If the lead loses teammate context after compaction, read `pr-tracking.json` to rediscover active PRs, then check each PR's state via `gh pr view` to rebuild the current situation.

**Lead behavior — reactive to teammate messages, runs smart-merge directly:**

No watcher agent. The lead checks PRs and merges directly when teammates report in. This is faster, more reliable, and saves tokens.

#### Teammate Messages (reactive)

- On teammate message "PR_CREATED": Update tracking, report to user: "PR #X for <tag>.<task-id> is ready for review"
- On teammate message "REVIEW_CLEAR": Run [Smart Merge](#smart-merge) for that PR
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

#### Smart Merge

The lead runs smart-merge directly — no watcher agent. Triggered by teammate "REVIEW_CLEAR" messages or human typing "check".

```bash
# Smart merge: check bot findings → dismiss stale → check merge state → merge
PR=<number>

# Step 1: Check stale bot CHANGES_REQUESTED reviews for valid findings
# CRITICAL: Don't blindly dismiss. Read the review comments first.
# Bots like CodeRabbit leave stale CHANGES_REQUESTED even after approving
# on re-review, but the original findings may still be valid.
STALE_REVIEWS=$(gh api repos/<owner>/<repo>/pulls/$PR/reviews \
  --jq '[.[] | select(.state == "CHANGES_REQUESTED" and (.user.login | endswith("[bot]")))]')

# For each stale bot review, read its comments
echo "$STALE_REVIEWS" | jq -r '.[].id' | while read REVIEW_ID; do
  # Read the review's inline comments
  COMMENTS=$(gh api repos/<owner>/<repo>/pulls/$PR/reviews/$REVIEW_ID/comments \
    --jq '.[] | {path, line, body: .body[0:200]}')

  # If comments exist, check if they were addressed by asking the teammate
  # or by reading the current code at those locations.
  # Only dismiss if concerns are addressed or the bot approved on a later pass.
  # If uncertain, message the teammate to verify before dismissing.

  gh api repos/<owner>/<repo>/pulls/$PR/reviews/$REVIEW_ID/dismissals \
    --method PUT -f message="Stale bot review — verified findings addressed" -f event="DISMISS"
done

# Step 2: Check merge state (mergeStateStatus is the real signal, not pending check counting)
gh pr view $PR --json mergeStateStatus,mergedAt,reviews \
  --jq '{
    mergeStateStatus,
    mergedAt,
    approvals: [.reviews[] | select(.state == "APPROVED")] | length,
    changesRequested: [.reviews[] | select(.state == "CHANGES_REQUESTED")] | length
  }'

# Step 3: Evaluate
# - mergedAt set → already merged (human merged externally)
# - mergeStateStatus == "CLEAN" AND approvals >= 2 AND changesRequested == 0 → merge
# - mergeStateStatus == "BLOCKED" / "BEHIND" / "DIRTY" → not ready, report why
```

**Auto-Merge Criteria (ALL must be true):**
1. `mergeStateStatus` is `"CLEAN"` (GitHub's own rollup: CI green, no conflicts, branch protections met)
2. At least 2 approvals (any combination of human or bot reviewers)
3. Zero non-dismissed changes-requested reviews

**After successful merge or detecting external merge:**

Lead runs cleanup directly (teammates are unreliable at post-merge cleanup):
```bash
# Lead runs cleanup — do NOT delegate to teammate
cd ~/dev/github.com/<org>/<repo>
task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=done
cd <repo>-main && git worktree remove --force ../worktree/<tag>/<task-id>--<slug>
git branch -d <tag>--<task-id>--<slug>
```

Then:
1. Report to user: "PR #X merged + cleaned up for <tag>.<task-id>"
2. **Shutdown the task teammate**: `SendMessage(type: "shutdown_request", recipient: "task-<task-id>", ...)`
3. Mark internal task completed via TaskUpdate
4. Check for newly unblocked tasks:
   ```bash
   task-master tags use "<tag>" && task-master list --ready --json
   ```
5. **Wave transition** — before spawning the next wave:
   - Review any BLOCKED or TOO_COMPLEX signals from the completed wave
   - Check if remaining tasks should be deferred or cancelled based on learnings
   - If earlier tasks revealed patterns, customize next teammate prompts with context (e.g., "Note: the codebase uses X pattern for Y, follow it")
6. Spawn fresh task teammates for any newly ready tasks (with adapted prompts)
7. If all tasks done → proceed to [Step 6: Completion](#step-6-completion)

**If not merge-ready:**
- `mergeStateStatus == "BLOCKED"` → report to user, message teammate to check CI / review requirements
- `mergeStateStatus == "DIRTY"` → message teammate: "PR has merge conflicts, resolve them"
- `changesRequested > 0` (non-bot) → report to user: "PR #X has human changes requested"

**Human can type "check"** to trigger smart-merge for all tracked PRs immediately.

**Idle teammate without message**: If a teammate goes idle without sending a meaningful message (PR_CREATED or REVIEW_CLEAR), check their PR status directly:
```bash
gh pr list --state open --json number,headRefName \
  --jq '.[] | select(.headRefName | test("<tag>--<task-id>"))'
```
If a PR exists, check its state and run smart-merge if eligible.

**Multi-PR wave merges**: When merging multiple PRs from the same wave, merge one at a time. After each merge, give remaining teammates a moment to sync with develop before checking their merge eligibility. This prevents false "CLEAN" states that flip to "DIRTY" mid-merge.

**Accidental input guard:** If the human sends an empty message, a single character, or the auto-suggested prompt text, treat it as a no-op. Reply with a brief status summary only — do NOT trigger expensive operations like merge checks or teammate messages. Only act on intentional commands like "check", "merged", or explicit instructions.

### Ephemeral Teammates Principle

Teammates are **one task, one session**. Task Master is the coordination brain — all state lives there, not in session context.

**Lifecycle of a teammate:**
```
Spawn (fresh) → Setup worktree → Implement (push commits) → Create PR (when complete) → Review loop → REVIEW_CLEAR → Idle
  → Lead runs smart-merge + cleanup directly → Shuts down teammate
```

The teammate stays alive through the review loop so the lead can message them about CI failures or merge conflicts. Once the lead merges and cleans up, the teammate is shut down — never reused for a different task.

**Why shutdown instead of reuse:**
- Shutdown is instant. Compaction/clearing is slow and unreliable.
- No context bloat from prior task's code, test output, review comments.
- Task Master already knows what to do next — the new session just reads it.
- Each task gets full context budget for its own work.

**Never reuse a teammate for a different task.** Always shutdown + spawn fresh.

### Lead Authority

The lead operates as a **tech lead running a sprint** — not a task router.

**Trusted decisions (no human approval needed):**
- Defer or cancel tasks that become irrelevant based on completed work
- Create new Task Master tasks discovered during the marathon (`task-master add-task --title="..." --description="..."`)
- Create follow-up tasks for tech debt or issues found during implementation
- Fix minor style/consistency nits across PRs directly (commit to the branch)
- Escalate model tier for a struggling teammate (shutdown + respawn on opus)
- Limit concurrency if teammates are creating merge conflicts with each other
- Reprioritize remaining tasks based on what earlier tasks revealed

**Always escalate to human:**
- Architectural changes not in the original task descriptions
- Public API surface modifications
- Deferring more than 30% of tasks in the tag
- Ambiguous reviewer feedback that could go either way

**Report judgment calls in status updates.** When the lead defers a task or changes approach, state what changed and why — this feeds into the retrospective.

### Step 6: Completion and Retrospective

When all tasks in tag are done:
1. Shutdown any remaining teammates via `SendMessage(type: "shutdown_request", ...)` (most will already be shut down from the merge cycle)
2. `TeamDelete()` to clean up team resources
3. **Run retrospective** — honest self-assessment of the marathon:

```
## Marathon Complete: <tag>

All <N> tasks done. <N> PRs merged.

### Retrospective

**What worked well**
- <specific patterns, tools, or approaches that were effective>

**What didn't work**
- <friction points, failures, wasted effort — be honest>

**Suggested tweaks**
- <concrete improvements to the /tm workflow based on this run>
  <reference specific sections of tm.md if applicable>

**Stats**
- Tasks: <N> completed, <N> cancelled/deferred
- PRs: <N> merged, <N> avg review iterations
- Teammates: <N> spawned, <N> needed manual intervention
- Merge friction: <stale reviews dismissed, conflicts resolved, etc.>
```

The retro should be **honest and specific** — not a summary of what happened, but an analysis of what the lead would do differently next time. Flag any patterns that should be codified back into `/tm`.

---

## Marathon Mode: Subagent Fallback

When `$MARATHON_MODE` is `true` but `$TEAMS_AVAILABLE` is `false`, the existing parallel subagent approach is used (see [Post-Cleanup Marathon Continuation](#step-3-post-cleanup---check-next-ready-marathon-mode) in the cleanup section).

---

## Orchestrator Flow

```
/tm [args...] → check Ralph → detect teams → parse args → route:
  │
  ├─ Args don't match any tag → PLANNING mode
  │   └─ Explore → PRD → create tag → parse tasks → complexity → expand → report → STOP
  │
  ├─ Marathon + Teams available → Agent Teams mode (dedicated section)
  │   ├─ Create team, spawn teammates for ready tasks
  │   ├─ Each teammate: setup worktree, implement, PR, review loop → REVIEW_CLEAR
  │   ├─ Lead: react to messages, smart-merge (dismiss stale bots, check mergeStateStatus, merge)
  │   ├─ Lead runs cleanup directly, shuts down teammate, spawns next wave
  │   └─ All done → retro → shutdown team
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
- **With Agent Teams**: Teammates implement + review loop. Lead runs smart-merge and cleanup directly — no watcher agent.
- **Without Agent Teams (fallback)**: Parallel subagents after each cleanup cycle.
- Lead auto-merges when mergeStateStatus CLEAN + ≥2 approvals + 0 changes requested (stale bot reviews auto-dismissed)
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

# Single-task mode:
Human merges PR     → (no command runs)
/tm after merge     → Parent: in-progress → done

# Marathon mode:
Lead smart-merges   → Lead runs cleanup → Task: in-progress → done
```

---

## Notes

- Lead runs smart-merge directly (no watcher agent) — dismiss stale bot reviews, check mergeStateStatus, merge
- Subagents can bail if work is too large
- `/tm` → report ready tasks
- `/tm <description that doesn't match a tag>` → planning mode (explore → PRD → tasks → expand → stop)
- `/tm <tag>` → marathon mode (work through entire tag)
- `/tm <tag> <task-id>` → single task mode
- **Marathon mode**: Runs continuously until all tasks in tag are done
  - Example: `/tm saga-script-versioning` works through entire tag
  - You merge PRs when ready → system auto-continues
  - Parallelizes independent tasks automatically
