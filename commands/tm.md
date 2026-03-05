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

## Phase 0: Detect Capabilities

```bash
# Ralph plugin
ls ~/.claude/plugins/cache/claude-plugins-official/ralph-loop/*/commands/ralph-loop.md 2>/dev/null && echo "RALPH_AVAILABLE" || echo "RALPH_NOT_AVAILABLE"

# Agent Teams
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
```

Set `$RALPH_AVAILABLE` and `$TEAMS_AVAILABLE` (`true` if result is `"1"`).

If Ralph not available, warn once:
> ⚠️ Ralph Loop plugin not installed. Using subagents (may burn context on long test runs). Install: `/plugin install ralph-loop`

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

| Condition | Action (Ralph) | Action (Fallback) |
|-----------|----------------|-------------------|
| `mergedAt` is set | cleanup-specialist | cleanup-specialist |
| PR exists, open | Ralph review loop | review-specialist |
| No PR | Ralph full cycle | implement-specialist |

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
git checkout develop && git pull origin develop
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

**CRITICAL: cd to repo root BEFORE launching subagent.** The subagent will delete the worktree.

```bash
cd ~/dev/github.com/<org>/<repo> && pwd
```

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
"""
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

1. **Branch in sync** — no merge conflicts with develop
2. **CI passing** — all checks succeed (or skipped)
3. **All inline comments addressed** — see thread resolution rules
4. **No unaddressed conversation comments**
5. **All review threads resolved**

**Thread resolution rules:**
- **CodeRabbit**: Fix code, push. Auto-resolves on re-review. **NEVER reply in CodeRabbit threads.**
- **claude[bot]**: Resolve via GraphQL `resolveReviewThread` if addressed
- **Human**: Fix code, reply inline, @mention reviewer. Do NOT resolve — let them confirm.

#### Each Iteration

**1. Sync with develop (FIRST):**
```bash
git fetch origin develop && git merge origin/develop --no-edit
```

**2. Check criteria:**
```bash
# CI
gh pr checks <number> --json state --jq '.[] | select(.state != "SUCCESS" and .state != "SKIPPED")' | head -1

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

#### Review Loop (Ralph or Fallback)

**CRITICAL**: Ralph args are passed to bash UNQUOTED. Shell special chars will break. Reference tasks by ID only.

**If Ralph available:**
```
Skill(
  skill: "ralph-loop:ralph-loop",
  args: "Review PR <number> for <tag>.<task-id> in <worktree-path>. FIRST merge origin/develop to stay in sync. Then loop until ALL green -- no merge conflicts, CI passing, no unresolved inline comments, conversation addressed, your review threads resolved. Fix issues, push, wait 60s, check again. --max-iterations 10 --completion-promise PR_READY --tag <tag> --task <task-id>"
)
```

**If Ralph NOT available:**
```
Task(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: """
# Review PR #<number> for <tag>.<task-id>

Working directory: <worktree-path>

Each iteration: merge origin/develop first. Loop until ALL green:
1. No merge conflicts  2. CI passing  3. No unresolved inline comments
4. No unaddressed conversation comments  5. All YOUR review threads resolved

Fix issues, push, wait for CI, repeat. Spawn opus for complex code changes.
Report: ready, waiting, or blocked.
"""
)
```

---

### Mode: Implement or Create PR

**If Ralph available:**
```
Skill(
  skill: "ralph-loop:ralph-loop",
  args: "Complete <tag>.<task-id> in <worktree-path>. Run task-master show <task-id> for requirements. TDD -- test, fix, commit. Push, create PR, then loop until ALL green. Each iteration merge origin/develop first, then check -- no merge conflicts, CI passing, no unresolved inline comments, conversation addressed, your review threads resolved. --max-iterations 20 --completion-promise PR_READY --tag <tag> --task <task-id>"
)
```

**If Ralph NOT available:**
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
"""
)
```

---

## Marathon Mode: Agent Teams

**Prerequisite:** `$MARATHON_MODE` AND `$TEAMS_AVAILABLE`.

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
5. **Detect shared-file conflicts** — force sequential execution for tasks modifying the same file
6. **Combine tasks aggressively** — merge into one teammate when:
   - Tightly coupled output (e.g., "add resources" + "add docs for resources")
   - Content-only tasks touching non-overlapping directories (e.g., adding 3 independent pattern dirs)
   - One is docs/config for the other, or one is meaningless without the other
   - Small tasks (complexity 1-2) that share a theme — PR-per-task overhead exceeds the work itself
7. **Identify hot files** — files touched by many tasks. Record as `$HOT_FILES` for:
   - Teammate prompts: include conflict resolution patterns
   - Merge ordering: hot-file PRs merge last
   - If 5+ tasks touch one file, consider a dedicated consolidation task
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
      "status": "working|review_clear|merge_pending|merged",
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
gh api repos/<owner>/<repo>/branches/develop/protection \
  --jq '.required_status_checks.contexts // []'
```
Store non-required check names in `meta.flaky_checks`.

### Step 3: Spawn Teammates

**Pre-spawn: Verify sacred dir is clean:**
```bash
cd ~/dev/github.com/<org>/<repo>/<repo>-main
git status --porcelain | head -5
```
If dirty (from unrelated work in another terminal), stash or warn before spawning. Teammates branch from this directory — dirty state propagates.

**Model selection:**
- Complexity 1-6: `model: "sonnet"`
- Complexity 7+: `model: "opus"`

Sonnet is the minimum — haiku cannot reliably handle review loops.

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

## Project Guidelines
<Include relevant sections from the repo's CLAUDE.md — testing patterns, coding standards, common
gotchas. This prevents discovering project conventions through CI failures.>

## Shell Pitfall
**Never use `gh ... --jq` with complex filters.** Always pipe to `jq`:
```bash
# WRONG: gh pr view --json reviews --jq '.reviews[] | select(.state != "APPROVED")'
# RIGHT:
gh pr view --json reviews | jq '.reviews[] | select(.state != "APPROVED")'
```

## Known Conflict Patterns
<If $HOT_FILES identified, include here>
- **Import/route files** (e.g., App.tsx, index.ts): Accept BOTH sides — additions are additive.
- **Barrel exports** (e.g., shared/index.ts): Accept both sides.
- **Config/manifest files**: Accept both sides unless same key modified differently (then escalate).

## Workflow
1. **Implement using TDD**: Write failing tests, make them pass, refactor. Commit incrementally.
2. **Push commits incrementally** for backup. Do NOT create a PR yet.
3. **Before creating a PR**, check if one already exists:
   ```bash
   gh pr list --head "<branch-name>" --state all --json number,state,mergedAt
   ```
   - PR exists and merged → your work is done. Message lead and wait idle.
   - PR exists and open → use that PR.
   - No PR → create one, then set TM status to review.
   - **After creating/finding PR**, verify state:
     ```bash
     gh pr view <number> --json state,mergeStateStatus | jq '{state, mergeStateStatus}'
     ```
     If DIRTY or BLOCKED, fix before entering review loop.
4. **Review loop**: Merge origin/develop first each iteration. Check all 5 green criteria:
   - No merge conflicts — resolve using patterns above
   - CI passing
   - No unresolved inline comments (fix for CodeRabbit — never reply in threads; reply to humans)
   - No unaddressed conversation comments
   - All your review threads resolved
5. Fix issues, push, wait 60s, check again.
   - **Merge conflicts are routine** — resolve using Known Conflict Patterns. Only escalate if genuinely ambiguous.
   - **After each push**, dismiss stale bot CHANGES_REQUESTED reviews:
     ```bash
     gh api repos/<owner>/<repo>/pulls/<number>/reviews \
       --jq '.[] | select(.state == "CHANGES_REQUESTED" and (.user.login | endswith("[bot]"))) | .id' \
     | while read RID; do gh api repos/<owner>/<repo>/pulls/<number>/reviews/$RID/dismissals \
       --method PUT -f message="Stale bot review" -f event="DISMISS"; done
     ```
6. **Before reporting REVIEW_CLEAR**, verify via API:
   ```bash
   gh pr view <number> --json mergeStateStatus,state | jq '{mergeStateStatus, state}'
   ```
   Only report if `mergeStateStatus` is CLEAN or UNSTABLE. If BLOCKED/DIRTY, fix and loop again.

## Communication
Only message the lead for **meaningful events**:
- PR created: `SendMessage(type: "message", recipient: "lead", content: "PR_CREATED: PR #<number> for <tag>.<task-id>", summary: "PR created <task-id>")`
- Review clear: `SendMessage(type: "message", recipient: "lead", content: "REVIEW_CLEAR: PR #<number> for <tag>.<task-id> — all criteria met", summary: "Review clear <task-id>")`
- **On MERGE_PENDING from lead**: Finish current atomic operation, then confirm: `SendMessage(type: "message", recipient: "lead", content: "IDLE_CONFIRMED: task-<task-id> idle, safe to merge", summary: "Idle confirmed <task-id>")`
- Blocked: `SendMessage(type: "message", recipient: "lead", content: "BLOCKED: <tag>.<task-id> — <reason>", summary: "Blocked <task-id>")`
- Too complex: `SendMessage(type: "message", recipient: "lead", content: "TOO_COMPLEX: <tag>.<task-id> — <brief reasoning>", summary: "Too complex <task-id>")`

## Lifecycle
1. Implement → push incrementally → create PR → review loop until green
2. Message lead REVIEW_CLEAR and wait idle
3. Lead handles merge, cleanup, and shuts you down
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
| REVIEW_CLEAR | Run [Smart Merge](#smart-merge) |
| IDLE_CONFIRMED | Proceed with merge |
| BLOCKED (merge conflicts) | Push back: "Resolve conflicts yourself" |
| BLOCKED (genuine) | Report to user, ask for guidance |
| TOO_COMPLEX | Shutdown teammate, decompose task, spawn fresh |

**On TOO_COMPLEX:**
1. Shutdown teammate, clean up failed worktree/branch
2. Decompose: `task-master expand --id=<task-id> --research` (subtasks) or cancel + create new peer tasks (sibling split)
3. Spawn fresh teammates for resulting tasks

**Unreliable REVIEW_CLEAR**: Don't rely solely on messages. **Proactively poll tracked PRs** when any teammate goes idle:
```bash
for PR in $(jq -r '.tasks | to_entries[] | select(.value.status == "working") | .value.pr' "$TRACK_FILE"); do
  gh pr view $PR --json mergeStateStatus,mergedAt,statusCheckRollup \
    | jq '{mergeStateStatus, mergedAt, checks: [.statusCheckRollup[] | select(.conclusion != "SUCCESS" and .conclusion != "SKIPPED" and .conclusion != null)] | length}'
done
```
If green with no unresolved threads, run smart-merge regardless of teammate message.

**Accidental input guard:** Empty messages, single characters, or auto-suggested prompt text → brief status summary only, no expensive operations.

#### Smart Merge

Triggered by REVIEW_CLEAR, idle teammate with green PR, or human typing "check".

**Known: Stale CodeRabbit CHANGES_REQUESTED.** When `request_changes_workflow` is enabled, CodeRabbit submits CR reviews. When it re-reviews and approves, GitHub does NOT dismiss the old CR. This is a GitHub limitation. Every PR needs stale CR dismissal. Don't investigate — just dismiss.

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
2. At least 2 approvals
3. Zero non-dismissed changes-requested reviews

**UNSTABLE handling:** If `mergeStateStatus == "UNSTABLE"`, check failing checks against `meta.flaky_checks`. If ALL failing checks are non-required, treat as merge-eligible. Report: "Merging with UNSTABLE — only non-required checks failing: <names>"

**Verify before merging** — never trust teammate claims:
```bash
gh pr view $PR --json state,mergedAt,mergeStateStatus | jq '{state, mergedAt, mergeStateStatus}'
```
Trust the API, not the message.

**Pre-merge teammate sync** — prevents orphaned commits:
```bash
# Persist intent in tracking (survives context compaction)
jq --arg task "task-<id>" '.tasks[$task].status = "merge_pending"' "$TRACK_FILE" > "$TRACK_FILE.tmp" && mv "$TRACK_FILE.tmp" "$TRACK_FILE"
```
```
SendMessage(type: "message", recipient: "task-<task-id>",
  content: "MERGE_PENDING: PR #<number> is merge-eligible. Confirm idle.",
  summary: "Confirm idle before merge")
```
Wait for IDLE_CONFIRMED before merging.

**After context compaction**: Check tracking for `merge_pending` tasks. If found and teammate is idle, proceed with merge (the handshake was already initiated).

**After merge:**
```bash
cd ~/dev/github.com/<org>/<repo>
task-master tags use "<tag>" && task-master set-status --id=<task-id> --status=done
cd <repo>-main && git worktree remove --force ../worktree/<tag>/<task-id>--<slug>
git branch -d <tag>--<task-id>--<slug>
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

1. Shutdown remaining teammates
2. `TeamDelete()`
3. Run retrospective:

```
## Marathon Complete: <tag>

All <N> tasks done. <N> PRs merged.

### Retrospective

**What worked well**
- <specific patterns, tools, or approaches>

**What didn't work**
- <friction points, failures — be honest>

**Suggested tweaks**
- <concrete improvements to /tm>

**Stats**
- Tasks: <N> completed, <N> cancelled/deferred
- PRs: <N> merged, <N> avg review iterations
- Teammates: <N> spawned, <N> needed intervention
- Merge friction: <stale reviews, conflicts, etc.>
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
  ├─ No PR → IMPLEMENT (Ralph or subagent)
  ├─ PR open → REVIEW (Ralph or subagent)
  ├─ PR merged → CLEANUP → (marathon? check next ready)
  │
  └─ No context → report ready tasks
```

---

## Task Status Reference

| Status | Meaning |
|--------|---------|
| pending | Not started |
| in-progress | Currently being worked on |
| review | Implementation complete, PR pending merge |
| done | PR merged, work verified |
| blocked | Cannot proceed due to external dependency |
| deferred | Postponed |
| cancelled | Will not be done |

**Lifecycle:**
```
Start → pending → in-progress
Subtask complete → review (NOT done)
PR merged → done (parent task)

Marathon: Lead smart-merges → cleanup → done
Single: Human merges → /tm → done
```
