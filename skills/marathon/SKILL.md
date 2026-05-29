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

## Step 3: Spawn Teammates

## Step 4: Lead Monitoring

## Smart Merge

## Crash Recovery

## Completion + Retrospective

## Subagent Fallback (no teams)
