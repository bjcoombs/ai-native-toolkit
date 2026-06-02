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
Run `/tm-marathon-config-example` to see the configuration template (it covers both /tm and /issues), then copy and customize it for your project.

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
  echo "EXISTING_TRACKING: reconciling against source of truth and GitHub"
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
<work unit requirements and subtasks — fetched via the adapter's enumerate operation for this unit id>

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
2. Decompose: expand the task into subtasks (or cancel + create new peer tasks for sibling split)
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

## Smart Merge

The lead runs smart-merge via the pr-review-merge skill (Smart Merge section): dismiss stale
bot CRs, verify the four auto-merge criteria, handle UNSTABLE/UNKNOWN, merge in hot-file order.
On a solo-maintainer repo (0 required approvals) merge with `gh pr merge $PR --squash --delete-branch --admin`
once the *required* checks are green — a plain merge gets bounced when a non-required check (CodeRabbit,
an advisory AI review, a regression gate that re-runs on base advance) is mid-run at the merge instant.
After a merge, close the unit via the adapter's **close on merge** operation, then proceed to
the wave transition below.

**Gate cleanup on a VERIFIED merge.** Worktree removal, branch deletion, and unit-close MUST run
only after confirming `gh pr view $PR --json state --jq '.state' == "MERGED"`. Never chain them
unconditionally after the merge call — a rejected merge with chained cleanup deletes the branch/worktree
of a PR that never merged (recoverable via the remote branch, but it wastes a recovery cycle every time).

**Don't merge an AI-authored docs/content PR while its AI reviewer is still pending.** Even when the
required checks are green and `mergeStateStatus` is CLEAN, wait for `claude[bot]`/`claude-review` to post —
AI-written docs are exactly where AI-authoring residue (leaked tool-envelope tags, duplicated sections)
hides, and the reviewer catches it. The minutes of waiting are cheaper than a follow-up PR + patch release.

**After merge:**
1. Report to user
2. Shutdown teammate (gate on verified `state == "MERGED"` first)
3. Mark internal task completed
4. Check for newly unblocked tasks
5. **Wave transition**: Batch-dismiss stale CRs across all eligible PRs before spawning next wave. Review signals from completed wave, adapt next prompts with learnings.
6. Check ready tasks via the adapter's enumerate operation filtered to `pending` status. Spawn fresh teammates for ready tasks.
7. If all done → [Completion](#completion--retrospective)

**If not merge-ready:**
- BLOCKED → report to user, message teammate
- DIRTY → message teammate: "resolve merge conflicts"
- Human changes requested → report to user

## Crash Recovery

If the session crashes, teammates linger as "active members" preventing `TeamDelete()`.

```bash
# 1. Force-remove stale team
rm -rf ~/.claude/teams/<tag>
rm -rf ~/.claude/tasks/<tag>

# 2. Check worktrees for uncommitted work
git worktree list | grep "<tag>"

# 3. Re-invoke the calling command (e.g. /tm <tag> or /issues <label>) — reconciliation handles stale tracking entries automatically.
```

Worktrees and `pr-tracking.json` survive crashes in `worktree/<tag>/`.

### Ephemeral Teammates

One task, one session. The work source is the coordination brain.

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
- Create new/follow-up tasks in the work source
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

## Completion + Retrospective

1. Send `shutdown_request` to each remaining teammate
2. Wait for all shutdown approvals
3. Call `TeamDelete()` - if it fails with "active members", verify panes are dead and retry. **TeamDelete is mandatory** - without it, teamContext persists and blocks future team creation in this session.
4. **PRD delivery check** — Re-read the original work units' acceptance criteria (PRD, issue bodies, or task details) and cross-reference against merged PRs. Report:
   - Criteria met (with PR evidence)
   - Criteria not met or partially met (flag for user)
   - Scope that was delivered beyond the original acceptance criteria (emergent work)
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

## Subagent Fallback (no teams)

When `$MARATHON_MODE` but `$TEAMS_AVAILABLE` is `false`, use parallel subagents after each cleanup cycle: enumerate next-ready units via the adapter's enumerate operation and spawn parallel subagents for each.
