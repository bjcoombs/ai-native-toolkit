# Design: GitHub-issue marathon (`/issues`) via shared marathon skills

Date: 2026-05-29
Status: Approved for implementation planning

## Problem

`/tm` runs a Task Master tag to completion: it derives a dependency DAG and hot-file
map, spins up an Agent Team, gives each task an ephemeral teammate, runs each PR
through a review-to-green loop, and smart-merges in waves. The orchestration logic is
mature - the in-file comments cite specific battle scars across multiple real marathons.

Two gaps:

1. There is no equivalent driver for **open GitHub issues**. A repo whose work lives in
   issues (not a Task Master tag) cannot use the marathon machinery.
2. The marathon engine and the PR review/merge logic live **inline inside `tm.md`** (890
   lines) and are **duplicated** in `fix-pr.md` (the 5 ready criteria, thread-resolution
   rules, shell pitfalls, background CI watcher are near-verbatim copies). Any fix to the
   review loop has to be made in two places and stays out of sync.

## Goal

- Add `/issues`: drive open GitHub issues to completion with the same marathon engine.
- Extract the source-agnostic logic into two **shared skills** so `/tm`, `/issues`,
  `/fix-pr` (and `/fix-develop` if it duplicates merge logic) all consume one source of
  truth.

## Non-goals

- No new orchestration *behaviour*. The extraction moves text verbatim; `/tm` must behave
  identically after the refactor.
- No standalone-skill (Claude Desktop / Cowork) distribution of the new skills - they are
  inherently Claude-Code-only (Agent Teams, subagents).
- No change to `/assess`, `/huddle`, `/deslop`, `/6hats`, `/understand`.

## Architecture

### Two new shared skills

```
skills/marathon/SKILL.md          Source-agnostic team orchestration engine.
skills/pr-review-merge/SKILL.md   Review-to-green loop + smart-merge.
```

`marathon` *composes* `pr-review-merge`: teammates run the review loop, the lead runs
smart-merge. Both are invoked by name via the Skill tool (works inside subagents).

### Four commands collapse onto them

```
commands/tm.md          REFACTORED. Keeps TM planning + TM adapter; delegates execution to marathon.
commands/issues.md      NEW. GitHub-issue triage + GitHub adapter; delegates execution to marathon.
commands/fix-pr.md       REFACTORED. Thin caller of pr-review-merge.
commands/fix-develop.md  REFACTORED if it duplicates merge logic (confirm during impl).
```

### The composition seam: the work-source adapter

`marathon` is identical regardless of where work comes from. Each front-end supplies an
adapter satisfying four operations:

| Operation | TM adapter (`/tm`) | GitHub adapter (`/issues`) |
|-----------|--------------------|-----------------------------|
| **enumerate** -> `{id,title,requirements,deps,complexity}` | `task-master list --json` for the tag | `gh issue list --label agent-ready --json` + `gh api .../dependencies/blocked_by` |
| **mark in-progress** | `set-status --status=in-progress` | add `in-progress` label |
| **close on merge** | `set-status --status=done` | PR body `Closes #N` -> GitHub auto-closes; engine verifies closed |
| **branch / worktree** | `<tag>--<id>--<slug>` in `worktree/<tag>/` | `issue-<N>--<slug>` in `worktree/issues/` |

The engine owns the hard logic (DAG analysis, hot-file combining, `TeamCreate`,
ephemeral-teammate template, waves, crash recovery, retro). The adapter is the
source-specific glue.

## `marathon` skill - extracted contents

Moved verbatim from `tm.md` "Marathon Mode: Agent Teams" and the worktree modes:

- Phase 0 capability detection (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, Marathon Configuration read)
- Step 1 DAG + hot-file analysis (combine-on-shared-file, fallback TM-style deps)
- Step 2 `TeamCreate` + `pr-tracking.json` + reconciliation + flaky-check detection
- Step 3 ephemeral-teammate spawn (model selection rules, teammate prompt template)
- Step 4 lead monitoring (reactive message table, polling, overlap rule, lead authority)
- Crash recovery
- Step 6 completion + retrospective
- Subagent fallback (no teams)

The teammate template and the lead's smart-merge step **reference `pr-review-merge`**
rather than embedding the review/merge text.

Adapter is passed in as a structured preamble the front-end fills before invoking the
skill (the four operations above, plus base-branch/approvals/bot-rules from Marathon
Configuration).

## `pr-review-merge` skill - extracted contents

Moved from `tm.md` Review Mode + Smart Merge and `fix-pr.md` (dedup'd):

- The 5 ready criteria (sync, CI incl. coverage gates, inline comments, conversation, threads)
- Thread-resolution rules (bot vs human; GraphQL jq-builder mutation)
- Shell pitfalls (pipe `gh` to `jq`; positive jq filters - zsh mangles `!=`)
- Base-sync-first each iteration + conflict-resolution patterns
- Background CI watcher pattern (never block on CI)
- Smart-merge: stale-bot-CR dismissal, auto-merge criteria, UNSTABLE/UNKNOWN handling,
  merge ordering by hot-file impact

Consumed by: `marathon` (teammates + lead), `/fix-pr` (single-PR loop), `/fix-develop`.

## `/issues` flow

Routing is decided by whether any `agent-ready` label already exists - mirroring `/tm`'s
plan-then-marathon two-step.

```
/issues [label-filter]
  │
  ├─ ANY open issue already labelled `agent-ready`  → MARATHON mode
  │     Work ONLY the agent-ready issues. Do NOT assess/touch untagged issues
  │     (the human has curated the queue by tagging).
  │
  └─ NO issue labelled `agent-ready`                → TRIAGE mode
        For each open issue (minus exclusions):
          ├─ clear enough     → add `agent-ready` label
          └─ ambiguous        → post clarifying questions as an issue COMMENT, add `needs-triage`
        Report what was tagged, then STOP and prompt:
          "Tagged #12,#15,#18 agent-ready; #20,#21 needs-triage (questions posted).
           OK to start on the agent-ready ones?"
        On go-ahead (or a re-invocation of /issues, which now finds tagged issues) → MARATHON mode.
```

### Marathon mode (after routing)

1. Enumerate `agent-ready` issues (GitHub adapter).
2. Build DAG: GitHub native `blocked_by`/`blocks` dependencies, then the same hot-file
   combining analysis `marathon` already does.
3. `TeamCreate`; spawn one ephemeral teammate per issue or combined group.
4. Teammates: implement (TDD) -> open PR with `Closes #N` (and `Closes #M` for combined
   issues) -> run `pr-review-merge` loop.
5. Lead: smart-merge in waves -> GitHub auto-closes the issue(s) on merge -> next wave.
6. Retrospective.

### Combined-issue PRs

When the engine combines two issues that share a hot file into one teammate, the PR body
carries `Closes #N` + `Closes #M` and closes both on merge. This is the conflict-avoidance
trick `/tm` credits with "0 conflicts across 3 marathons" and is preserved.

## Configuration

The project's `## Marathon Configuration` (in its CLAUDE.md) gains a GitHub-issues
subsection. New settings with defaults:

| Setting | Default | Meaning |
|---------|---------|---------|
| Agent-ready label | `agent-ready` | Opt-in label that marks an issue marathon-eligible |
| Needs-triage label | `needs-triage` | Applied with clarifying-question comment |
| In-progress label | `in-progress` | Applied when a teammate starts an issue |
| Issue exclude labels | (none) | Labels that exclude an issue from triage |

`commands/tm-marathon-config-example.md` gains the GitHub Issues subsection. Existing
base-branch / approvals / bot-rules settings are shared by both commands unchanged.

## Versioning

New command + two new skills = **MINOR** bump in `.claude-plugin/plugin.json`, in the same
PR (per repo CLAUDE.md).

## Standalone-skill build

`marathon` and `pr-review-merge` are **not** added to the standalone build list
(`scripts/build-standalone-skills.sh`). They depend on Claude-Code-only tools (Agent
Teams, subagents) and have no meaning in Claude Desktop chat. No chat-skip markers needed.

## Testing

Markdown skills/commands have two testable halves, and they belong in different places:

- **Behaviour** (does `/tm` actually run a marathon?) requires executing an LLM. There is no
  deterministic way to assert it, so it stays **out of CI** and is covered by the human-run
  validation marathon (acceptance gate below). No AI is added to the CI workflow.
- **Contract / structure** is fully deterministic and goes in CI. Today the repo's CLAUDE.md
  *documents* invariants as prose but nothing enforces them. This work adds a **pytest
  contract-test harness** that encodes them as executable assertions, wired into the existing
  `.github/workflows/tests.yml` as a third job (same `uv run --with pytest pytest` pattern as
  `skills/assess/` and `scripts/`). No new language or toolchain.

The harness lives in a new root `tests/` dir (`tests/test_plugin_contract.py`) and walks
`skills/`, `commands/`, `agents/`, and `.claude-plugin/`. Scope: **contract + references**.
It asserts, for every skill and command:

- frontmatter parses; skill `name:` matches its directory; `description` present; a `TRIGGER`
  clause present where CLAUDE.md requires one (skills);
- no placeholder tokens (`TODO`/`TBD`/`FIXME`) in shipped `.md` files;
- every internal `[text](relative/path)` link resolves to a real file;
- every `Use the <name> skill` reference resolves to an existing `skills/<name>/SKILL.md`;
- every command that names an agent points at an existing `agents/<name>.md`;
- `plugin.json` is valid JSON; every `marketplace.json` entry exists on disk;
- `marathon` and `pr-review-merge` are absent from the standalone build list.

This converts the one-off anchor-greps the extraction would otherwise rely on into permanent
CI guards: any future edit that breaks a reference or drops required frontmatter fails the PR.

## Risk and acceptance gate

The blast radius is the `/tm` refactor: it is battle-tested and the extraction must not
change its behaviour.

Mitigation - extract by **moving text verbatim**:

1. Move marathon + review/merge sections into the two skills with no edits.
2. Rewire `tm.md`, `fix-pr.md`, `fix-develop.md` to invoke the skills.
3. Diff-review: the assembled instructions a consumer now sees must match the original
   inline text (modulo the adapter preamble).

**Acceptance gate:** a real validation marathon on a live tag with `/tm` after the
refactor, confirming identical behaviour (DAG analysis, waves, smart-merge, retro), plus a
first `/issues` run against a repo with labelled issues.

## Open implementation details (resolve during planning, not blocking)

- Exact mechanism for passing the adapter into the skill (structured preamble block vs the
  front-end pre-substituting placeholders).
- Whether `/fix-develop` genuinely duplicates merge logic (read it during impl; refactor
  only if it does).
- Whether `marathon`/`pr-review-merge` need TRIGGER clauses for direct user invocation or
  are purely command-invoked library skills (repo CLAUDE.md requires a TRIGGER clause; for
  library skills the description notes the consuming commands and still carries a TRIGGER).
- `worktree/issues/` layout when issues span milestones (single dir vs per-milestone).
