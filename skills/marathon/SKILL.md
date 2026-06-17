---
name: marathon
description: >
  Run a list of work units to completion with an Agent Team: derive a dependency DAG and
  hot-file map, spawn one ephemeral teammate per unit (or combined group), drive each PR
  through pr-review-merge, smart-merge in waves, recover from crashes, and run a
  retrospective. Source-agnostic — the caller supplies a work-source adapter. A library
  skill invoked BY the /tm and /issues commands, not run directly by a user (it needs a
  caller-supplied adapter). TRIGGER when a command needs autonomous multi-unit team
  orchestration to completion — a tag, issue queue, backlog, or set of tickets run to done
  with Agent Teams. For a single PR use pr-review-merge instead; not for one-off single-task
  work.
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

If no Marathon Configuration section exists, **advise the user to set one up — this is non-blocking; emit the notice and proceed with defaults** (do not wait for an answer):
```
No Marathon Configuration found in this project's CLAUDE.md.

For best results, add a ## Marathon Configuration section to your project's CLAUDE.md.
Run `/tm-marathon-config-example` to see the configuration template (it covers both /tm and /issues), then copy and customize it for your project.

Proceeding with defaults: base branch=main, 1 approval, no bot reviewer rules.
```

Defaults apply for non-marathon use (single task mode, planning mode) without prompting. The template below uses `$BASE_BRANCH` where previous versions hardcoded `develop`.

## Execution Modes

The steps below are written for **team mode** — the lead chairs an Agent Team, spawns one ephemeral teammate per unit, and coordinates via `SendMessage` and `shutdown_request`. Phase 0's `$TEAMS_AVAILABLE` selects the mode:

| Mode | When | How the body maps |
|------|------|-------------------|
| **Team** | `$TEAMS_AVAILABLE` true | Run the body as written: spawn one background teammate (`Agent` with `run_in_background`) per unit/combined group into the session's single implicit team, message-driven monitoring. |
| **Phased sub-agent** | `$TEAMS_AVAILABLE` false | No persistent team and no `SendMessage`. The lead runs each wave as a batch of parallel subagents, reads their returned transcripts in place of messages, and drives the same loop. See [Subagent Fallback](#subagent-fallback-no-teams). |

Everything else — the DAG analysis, hot-file combining, tracking file, smart-merge, crash recovery, and retrospective — is identical across modes; only the teammate-coordination mechanism differs. Where a step is team-only (the `SendMessage` events, early-shutdown, and idle-ping handling), the phased fallback simply has no equivalent: subagents return rather than message.

## Step 1: DAG + Hot-File Analysis

**CRITICAL: Global Source-of-Truth Write Rule**
Never run source-of-truth write commands as parallel background jobs — concurrent writes race. Each such command may internally switch global state, and concurrent invocations can silently land work on wrong targets. Always run source-of-truth write commands **sequentially inline** — 10 concurrent background `add-task` calls once landed tasks on the wrong tags.

Enumerate work units via the adapter's **enumerate** operation.

**Analyze dependency tree for maximum concurrency:**

1. Map the dependency tree — which tasks block which?
2. Identify the critical path (longest sequential chain)
3. **Challenge unnecessary dependencies** — different files/modules may not need sequencing
4. Look for tasks chained sequentially that could run in parallel
5. **Identify hot files** — files touched by multiple tasks. Record as `$HOT_FILES`.
6. **Primary mitigation: combine tasks that share hot files** into one teammate. Combined units share one branch and worktree, so there is no inter-unit merge and the conflict class is eliminated entirely. Combine when:
   - Tasks share hot files (strongest signal — prefer combining over dependency management for *small, coupled* tasks)
   - Tightly coupled output (e.g., "add resources" + "add docs for resources")
   - Content-only tasks touching non-overlapping directories (e.g., adding 3 independent pattern dirs)
   - One is docs/config for the other, or one is meaningless without the other
   - Small tasks (complexity 1-2) that share a theme — PR-per-task overhead exceeds the work itself

   **Combining has a ceiling — it must not swallow the parallelism it exists to protect.** Combining buys zero conflicts by trading away concurrency, so it only pays while the combined unit stays small and genuinely coupled. A hot file is a combine *candidate*, not a combine *mandate*. Do NOT combine when it would:
   - push the combined unit past ~complexity 8 — one teammate then serially implements a large PR, which is slower than parallel teammates each resolving an additive conflict;
   - collapse the wave — if combining would leave fewer than 2 parallel units where the DAG allowed more, you have destroyed the wave, not optimized it; use dependencies instead;
   - fold in a task that *depends on* the others, or a complexity-8+ task — a dependency is a sequencing signal, not a combine signal. Sequence it across PRs; don't serialize it inside one.

   Some hot files are touched by *every* PR and want **sequential merge, never combining**: a version counter (`.claude-plugin/plugin.json` `.version`), a changelog, a lockfile. Assign each teammate its target value explicitly at spawn and merge in order (highest version wins) — combining all PRs to dodge a one-line version conflict is the trap, not the fix.

   **Version values assigned at spawn are final — never re-message a new version to an in-flight teammate** (it races with PR_CREATED/REVIEW_CLEAR and produces crossed-message churn). If readiness order ends up differing from the planned merge order, that is handled at merge time, not by re-messaging — see [Smart Merge](#smart-merge).

   **One caveat overrides "identical bumps merge cleanly":** if the repo auto-publishes an *immutable per-version artifact* on a version *change* (e.g. a `standalone-skills-v<version>` build that fires on the `plugin.json` bump), identical bumps across parallel PRs silently break it — the first merge fires the build from an incomplete tree and permanently consumes that version's tag, and the later identical bumps don't change the version so the build never re-fires. There, do **not** use identical bumps: have the **last-merging PR bump one step higher** (or bump once at the very end, after all merges) so the complete tree republishes.
7. **Fallback: dependencies** — when combining isn't feasible or would breach the ceiling above (any task complexity 8+, a real dependency between the tasks, fundamentally different concerns despite a shared file, or 5+ tasks on one file):
   - **Add explicit dependencies** — merge the simpler/faster task first, then the other depends on it.
   - Teammate prompts: include conflict resolution patterns
   - If 5+ tasks touch one file, decide by the *kind* of contention. This additive-vs-serialize split is a **5+-on-one-file rule and does not override Step 1.6 below that threshold**: a small coupled pair (2-4 tasks) sharing one additive hot file under the complexity ceiling still **combines** — combining is the primary mechanic, it yields 0 conflicts, and it costs only one parallel slot. At 5+ the arithmetic flips: **purely additive** edits (schema appends, barrel exports, route registration — the "accept both sides" cases) are cheap to merge, so keep the units **parallel** in one wave and merge them in order rather than collapsing four-plus parallel slots into one teammate; do NOT serialize them. Reserve the **dedicated consolidation task** (one teammate owns that file; the others depend on it) for **same-line or structural** contention where parallel edits would genuinely conflict — and even then, prefer it over folding all 5+ into one mega-PR.
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

This build uses a **single implicit team** - there is no `TeamCreate` step. The team forms as you spawn named background teammates (next): each `Agent(name: "task-<id>", run_in_background: true)` joins the session's implicit team and is addressable via `SendMessage(to: "task-<id>")`. Proceed straight to tracking.

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

**Pre-spawn: Read the retro log's open template changes:**
Before writing any spawn prompt, read the retro log (Marathon Configuration `$RETRO_LOG`) Template Changes table — **skip this step entirely if `$RETRO_LOG` is unset** (defaults supply none), the same escape the completion-time read uses. Apply every row still marked Pending to this run's spawn prompts and lead behaviour now - that is what the table is for. Reading these only at retro time is too late — the same friction then recurs the whole run, which is exactly how past fixes sat unapplied across entire marathons before shipping.

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

Sonnet is cost-effective but has a recurring false REVIEW_CLEAR problem — reports review-clear without verifying all criteria. Opus has not shown this. When in doubt, use opus — the cost delta is cheaper than intervention time.

Haiku cannot reliably handle review loops — never use for teammates.

**Combined-group identity:** combining is the primary mechanic, so a teammate often covers several units. Give a combined group one identity derived from its member ids: name `task-<id>+<id>` (e.g. `task-1+2`), branch `<tag>--<id>+<id>--<slug>`, worktree `worktree/<tag>/<id>+<id>--<slug>`; its complexity is the sum of its members'. The Scope guard and the activity-check `find` path below operate on this combined branch/worktree — substitute the combined id wherever the singular `<task-id>` appears. Mark each member unit in-progress and close each on merge.

**Teammate prompt template:**
```
Agent(
  subagent_type: "general-purpose",
  run_in_background: true,
  name: "task-<task-id>",   # combined group: task-<id>+<id> (see Combined-group identity above)
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
1. **Implement using TDD**. Push commits incrementally for backup. If your change touched a module that has a documented co-change partner — a sibling doc or a seam-map README named in your Project Guidelines — update it in the same PR. A code change without its paired doc is a lying map and a predictable review thread; updating it now is cheaper than a follow-up cycle after REVIEW_CLEAR.
2. **Before creating PR**, check for existing: `gh pr list --head "<branch-name>" --state all --json number,state,mergedAt`
   - Merged → message lead, wait idle. Open → use it. None → create one.
3. **Get required checks green, then stand down.** Use the pr-review-merge skill's criteria and thread rules to fix any failing *required* checks and resolve any bot threads already posted, pushing fixes. Then report and go idle. **Do NOT run a `gh pr checks --watch` loop or any background CI watcher** - in marathon mode the lead owns CI watching, the slow `claude-review`/AI-review wait, and the merge. A teammate that watches a slow advisory check sits idle for minutes and floods the lead with idle notifications; that is the lead's job here, not yours. While your PR is not yet at required-green the lead may message you to fix a failing check or thread - respond and push. Once you send REVIEW_CLEAR you are done: the lead does not re-wake you, it spawns a fresh teammate if more work surfaces (one task, one teammate).

## Communication
Only message the lead for **meaningful events**:
- PR created: `SendMessage(type: "message", recipient: "lead", content: "PR_CREATED: PR #<number> for <tag>.<task-id>", summary: "PR created <task-id>")`
- Review clear: `SendMessage(type: "message", recipient: "lead", content: "REVIEW_CLEAR: PR #<number> for <tag>.<task-id> — required checks green, threads resolved, standing down (lead owns claude-review wait + merge)", summary: "Review clear <task-id>")`
- Blocked: `SendMessage(type: "message", recipient: "lead", content: "BLOCKED: <tag>.<task-id> — <reason>", summary: "Blocked <task-id>")`
- Too complex: `SendMessage(type: "message", recipient: "lead", content: "TOO_COMPLEX: <tag>.<task-id> — <brief reasoning>", summary: "Too complex <task-id>")`

## Scope
- Only create PRs on YOUR branch (`<tag>--<task-id>--<slug>`, or the combined-group branch `<tag>--<id>+<id>--<slug>` if you cover several units). Never create PRs on other branches or for work outside your assigned task(s).
- If you discover related work that needs doing, mention it in your PR description — don't create additional PRs.

## Lifecycle
1. Implement → push incrementally → create PR → message lead PR_CREATED
2. Fix any failing **required** checks and any already-posted bot threads; push. Do NOT watch CI - the lead owns that.
3. Message lead REVIEW_CLEAR (required checks green, threads resolved) and stand down. Do not sit through the slow `claude-review`/AI-review window - that wait is the lead's to hold.
4. The lead owns the claude-review wait + merge, cleans up, and shuts you down at green. After REVIEW_CLEAR you are not re-woken - if more work surfaces the lead spawns a fresh teammate (one task, one teammate). **Approve the lead's `shutdown_request` promptly when it arrives, and after REVIEW_CLEAR do NOT idle-ping or re-send merge-readiness nudges** — the lead owns the merge; re-nudging an already-cleared PR just churns the lead while it holds the claude-review wait.
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
| PR_CREATED | Update tracking, report to user, and **start owning the CI watch for this PR** (the teammate does not watch it) |
| REVIEW_CLEAR | Verify required-green + threads, then **shut the teammate down immediately** (don't leave it idle through the claude-review wait), hold for claude-review per the AI-docs rule, and run [Smart Merge](#smart-merge) |
| CLARIFICATION_NEEDED | Answer from task context, or relay to user if genuinely ambiguous |
| BLOCKED (merge conflicts) | Push back: "Resolve conflicts yourself" |
| BLOCKED (genuine) | Report to user, ask for guidance |
| TOO_COMPLEX | Shutdown teammate, decompose task, spawn fresh |

**On TOO_COMPLEX:**
1. Shutdown teammate, clean up failed worktree/branch
2. Decompose: expand the task into subtasks (or cancel + create new peer tasks for sibling split)
3. Spawn fresh teammates for resulting tasks

**Shut teammates down early to kill idle churn**: The moment a teammate's PR has required checks green and threads resolved (its REVIEW_CLEAR, or your own poll showing it), shut the teammate down - do not leave it idle through the claude-review wait and the merge. The lead owns that tail. A live-but-idle teammate emits a continuous stream of idle notifications (the harness re-pings idle members), which is pure attention-drain on the lead; early shutdown is the fix, not patience. This is also why teammates are told not to run their own CI watcher - the lead watches, the lead merges, the teammate is gone before the slow advisory checks finish. If you have sent a `shutdown_request` and the teammate keeps emitting idle notifications without approving it, re-send the request once rather than sitting through the idle stream — the re-send re-prompts it to process the approval.

**Lead conflict resolution**: When a teammate is idle and their PR is DIRTY (merge conflict), resolve it directly instead of nudging the teammate. Pull `$BASE_BRANCH`, resolve the conflict, push. Faster than round-tripping to an idle teammate (~10 min saved per conflict). This idle-DIRTY conflict is the **sole** work the lead executes directly — it does not generalize: a failing test, missing implementation, or thread fix is still delegated per [Lead Authority](#lead-authority), even when it looks like a quick edit. If the teammate whose branch you're resolving may still be live (not yet idle/down), message it *before* you push — "leave the version conflict to me, I'm resolving" — then push; pushing first races with the teammate resolving the same conflict in its own worktree.

**Check `mergeStateStatus` before watching CI on a late-wave PR.** A PR branched before its siblings merged can be DIRTY against an advanced `$BASE_BRANCH`; GitHub computes no merge ref for a DIRTY PR, so the test workflow never registers and a CI watcher waits forever for checks that cannot appear. On PR_CREATED for any PR that may sit behind already-merged siblings, check `mergeStateStatus` first and resolve DIRTY before starting the watch.

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

**Version-counter conflicts resolve here, never by re-messaging.** Each teammate's target version
was fixed at spawn (Step 1). When a PR becomes ready out of the planned merge order — usually because
an externally-merged PR advanced the base mid-run — either hold the lower-version PR until its
predecessor merges, or lead-resolve the `plugin.json` conflict to the highest version (per
[Lead conflict resolution](#step-4-lead-monitoring)). Re-messaging a new version to an in-flight
teammate races with its own PR_CREATED/REVIEW_CLEAR — don't.

**Teammate shutdown and cleanup are two separate stages — don't conflate them.** The teammate is shut
down *early*, at REVIEW_CLEAR (see [Step 4](#step-4-lead-monitoring)) — the moment its PR is required-green
with threads resolved, never held live through the claude-review wait or the merge. *Cleanup* (worktree
removal, branch deletion, unit-close) is the *later* stage and is the only thing the verified-MERGED gate
guards. By the time you merge, the teammate is already gone; the post-merge step below is a confirm-pane-dead
check, not a second shutdown.

**Gate cleanup on a VERIFIED merge.** Worktree removal, branch deletion, and unit-close MUST run
only after confirming `gh pr view $PR --json state --jq '.state' == "MERGED"`. Never chain them
unconditionally after the merge call — a rejected merge with chained cleanup deletes the branch/worktree
of a PR that never merged (recoverable via the remote branch, but it wastes a recovery cycle every time).

**Don't merge an AI-authored docs/content PR while its AI reviewer is still pending.** Even when the
required checks are green and `mergeStateStatus` is CLEAN, wait for `claude[bot]`/`claude-review` to post —
AI-written docs are exactly where AI-authoring residue (leaked tool-envelope tags, duplicated sections)
hides, and the reviewer catches it. The minutes of waiting are cheaper than a follow-up PR + patch release.

**Any push after REVIEW_CLEAR re-opens the verify gate.** A lead conflict-resolution, a base-advance
re-trigger, or a late fix all produce a new head, and bots re-review that new commit — a reviewer that
passed clean on the prior head can post a fresh finding on this one. After any post-REVIEW_CLEAR push,
re-check the required checks *and* unresolved threads on the new head before merging; never merge on the
strength of the earlier REVIEW_CLEAR alone.

**After merge:**
1. Report to user
2. Confirm the teammate is already down — you stood it down at REVIEW_CLEAR; this is a confirm-pane-dead check, not a second shutdown, and is **not** gated on the merge. The verified-`MERGED` gate below guards *cleanup* (step 3 onward), not the shutdown.
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

If the session crashes, background teammates may linger as active members. The implicit team has no `TeamDelete` to block, but stale team/task state under `~/.claude/` should still be cleared so a re-run starts clean.

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
Spawn → Setup worktree → Implement → Create PR → required checks green + threads resolved → REVIEW_CLEAR
  → Lead: shut teammate down → Lead owns claude-review wait → smart-merge + cleanup
```

Never reuse a teammate for a different task. Shutdown + spawn fresh.

### Lead Authority

The lead operates as a **tech lead running a sprint** — not a task router.

**The lead delegates, never executes.** During a marathon, the lead's job is to coordinate: merge PRs, spawn teammates, monitor status. Any work that takes more than ~30 seconds (tests, coverage, code exploration, implementation, thread resolution) must be delegated to a teammate or subagent. The one carve-out is resolving a DIRTY merge conflict for an *idle* teammate (see [Lead conflict resolution](#step-4-lead-monitoring)) — that, and nothing else, the lead does directly. The lead must stay responsive to teammate messages at all times. The moment the lead starts executing, messages queue up and momentum stalls.

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
3. The implicit team has no `TeamDelete` - once every teammate has approved shutdown or already exited, the team is gone. If a stale background teammate lingers, verify its pane/process is dead; nothing persists to block a future marathon.
4. **PRD delivery check** — Re-read the original work units' acceptance criteria (PRD, issue bodies, or task details) and cross-reference against merged PRs. Report:
   - Criteria met (with PR evidence)
   - Criteria not met or partially met (flag for user)
   - Scope that was delivered beyond the original acceptance criteria (emergent work)
5. Read the retro log (path from Marathon Configuration `$RETRO_LOG`, or skip if not configured)
6. Run retrospective using the structured format below
7. Append this marathon to the retro log (Marathon History + update Template Changes validation)

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

When `$TEAMS_AVAILABLE` is `false`, run the same loop without a persistent team (see [Execution Modes](#execution-modes)). Per wave: enumerate the next-ready units via the adapter, spawn one parallel subagent per unit (or combined group), and wait for their returned transcripts in place of `SendMessage` events. The lead still owns CI watching, smart-merge, and cleanup exactly as in team mode, and the tracking file (Step 2) carries cross-wave state in place of live teammates. There is no `shutdown_request` or idle-churn to manage — subagents return when done — so the early-shutdown and idle-ping guidance simply has no equivalent here.
