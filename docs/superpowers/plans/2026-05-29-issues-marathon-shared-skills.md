# GitHub-issue marathon (`/issues`) + shared marathon skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/issues` command that drives open GitHub issues to completion with the same Agent-Team marathon engine `/tm` uses, by extracting the source-agnostic orchestration and PR-review/merge logic into two shared skills that `/tm`, `/issues`, `/fix-pr`, and `/fix-develop` all consume.

**Architecture:** Extract two new skills — `marathon` (team orchestration engine) and `pr-review-merge` (review-to-green loop + smart-merge) — from `commands/tm.md`. `marathon` composes `pr-review-merge`. Each command supplies a thin **work-source adapter** (TM tasks vs GitHub issues) and delegates execution to the skills. Extraction is **verbatim text-move** to preserve `/tm`'s battle-tested behaviour; the acceptance gate is a diff-review proving the assembled instructions a consumer sees match the original.

**Tech Stack:** Markdown skills/commands for Claude Code; `gh` CLI + GraphQL; `jq`; Task Master CLI; Agent Teams (`TeamCreate`/`SendMessage`/`TeamDelete`), `Agent` subagents. Verification is a **deterministic pytest contract-test harness** (`tests/test_plugin_contract.py`, wired into `tests.yml` — no AI, no new language) checking frontmatter, reference resolution, placeholders, and plugin invariants, plus the verbatim-extraction diff-review and the human-run validation marathon (behaviour can't be asserted without an LLM, so it stays out of CI).

**Spec:** `docs/superpowers/specs/2026-05-29-issues-marathon-shared-skills-design.md`

---

## File Structure

**Created:**
- `tests/test_plugin_contract.py` — deterministic contract + reference harness for all skills/commands
- `skills/marathon/SKILL.md` — source-agnostic orchestration engine + adapter contract
- `skills/pr-review-merge/SKILL.md` — review-to-green loop + smart-merge
- `commands/issues.md` — GitHub-issue triage + GitHub adapter; delegates to `marathon`

**Modified:**
- `.github/workflows/tests.yml` — add a third pytest job running the contract harness from repo root
- `commands/tm.md` — keeps Planning Mode + TM adapter; marathon/review/merge sections replaced by Skill invocations
- `commands/fix-pr.md` — thin caller of `pr-review-merge`
- `commands/fix-develop.md` — keeps default-branch diagnosis; review loop delegated to `pr-review-merge`
- `commands/tm-marathon-config-example.md` — adds GitHub Issues config subsection
- `.claude-plugin/plugin.json` — MINOR version bump
- `README.md` — document `/issues` and the two shared skills

**Ordering rationale:** the contract harness first (Task 0) so it goes green against the current repo and then guards every file added after it; `pr-review-merge` next (no skill dependencies), then `marathon` (depends on it), then the command rewires (depend on both), then `/issues` (new front-end), then config/version/docs, then the validation gate.

---

## Conventions for this plan

**"Move verbatim" means:** copy the exact lines from the source file into the target skill section with **zero wording changes** except (a) replacing TM-specific commands with the adapter operation name, and (b) replacing an inline block with a `Use the <skill> skill` invocation where noted. Any other change is out of scope for the extraction tasks.

**Skill invocation from a command/teammate:** a consumer triggers a shared skill by including a line the model acts on, e.g. `Use the pr-review-merge skill to drive PR #<n> to green.` Subagents and teammates can invoke skills the same way (the Skill tool works inside subagents). Where a teammate prompt previously embedded the review loop inline, it now says: `Use the pr-review-merge skill for the review loop (5 criteria, thread rules, background CI watcher).`

**Verification via the contract harness:** once Task 0 lands, every later task ends with `uv run --with pytest pytest -v tests/` (run from repo root) in addition to its targeted grep checks. The harness is the deterministic guard; the greps are quick local confirmations. The repo's existing suites (`skills/assess/`, `scripts/`) are untouched by this work.

---

## Task 0: Build the deterministic contract-test harness and wire it into CI (do first)

**Files:**
- Create: `tests/test_plugin_contract.py`
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 1: Write the contract harness**

Create `tests/test_plugin_contract.py` with exactly this content:

```python
"""Deterministic contract + reference checks for the plugin's skills and commands.

No AI, no network. Encodes the invariants documented in CLAUDE.md as executable
assertions so a broken reference or dropped frontmatter fails the PR.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
COMMANDS = REPO / "commands"
AGENTS = REPO / "agents"
PLUGIN = REPO / ".claude-plugin"

# Skills referenced by name that live outside this plugin (superpowers, etc.).
EXTERNAL_SKILLS = {
    "brainstorming", "writing-plans", "executing-plans",
    "subagent-driven-development", "using-superpowers",
}
# subagent_type values that are built into Claude Code, not agents/*.md.
BUILTIN_AGENTS = {"general-purpose", "Explore", "Plan", "statusline-setup"}

PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME)\b")
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
USE_SKILL_RE = re.compile(r"[Uu]se the ([a-z0-9][a-z0-9-]*) skill")
SUBAGENT_RE = re.compile(r'subagent_type:\s*"([^"]+)"')


def _split_frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    return text[3:end], text[end + 4:]


def _fm_scalar(fm: str, key: str):
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*(.*)$", fm)
    return m.group(1).strip() if m else None


def skill_dirs():
    if not SKILLS.is_dir():
        return []
    return sorted(d for d in SKILLS.iterdir() if (d / "SKILL.md").is_file())


def command_files():
    return sorted(COMMANDS.glob("*.md")) if COMMANDS.is_dir() else []


def shipped_md():
    return [d / "SKILL.md" for d in skill_dirs()] + command_files()


def known_skill_names():
    return {d.name for d in skill_dirs()} | EXTERNAL_SKILLS


def known_agent_names():
    names = {p.stem for p in AGENTS.glob("*.md")} if AGENTS.is_dir() else set()
    return names | BUILTIN_AGENTS


@pytest.mark.parametrize("d", skill_dirs(), ids=lambda d: d.name)
def test_skill_frontmatter(d):
    fm, _ = _split_frontmatter(d / "SKILL.md")
    assert fm is not None, f"{d.name}/SKILL.md missing YAML frontmatter"
    assert _fm_scalar(fm, "name") == d.name, f"{d.name}: name: must match directory"
    desc = fm[fm.find("description:"):] if "description:" in fm else ""
    assert "description:" in fm and desc.strip(), f"{d.name}: description required"


@pytest.mark.parametrize("d", skill_dirs(), ids=lambda d: d.name)
def test_skill_has_trigger_clause(d):
    fm, _ = _split_frontmatter(d / "SKILL.md")
    assert fm and "TRIGGER" in fm, f"{d.name}: description must include a TRIGGER clause"


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_placeholder_tokens(p):
    body = FENCE_RE.sub("", p.read_text(encoding="utf-8"))
    assert not PLACEHOLDER_RE.search(body), f"{p.relative_to(REPO)}: placeholder token outside code fence"


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_internal_links_resolve(p):
    for target in LINK_RE.findall(p.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#", "mailto:")) or "$" in target or "<" in target:
            continue
        rel = target.split("#", 1)[0]
        if not rel:
            continue
        assert (p.parent / rel).resolve().exists(), f"{p.relative_to(REPO)}: dead link -> {target}"


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_use_the_skill_references_resolve(p):
    known = known_skill_names()
    for name in USE_SKILL_RE.findall(p.read_text(encoding="utf-8")):
        assert name in known, f"{p.relative_to(REPO)}: 'Use the {name} skill' references unknown skill"


@pytest.mark.parametrize("p", command_files(), ids=lambda p: p.name)
def test_subagent_types_resolve(p):
    known = known_agent_names()
    for name in SUBAGENT_RE.findall(p.read_text(encoding="utf-8")):
        if "<" in name:  # template placeholder like task-<task-id>
            continue
        assert name in known, f"{p.name}: subagent_type \"{name}\" has no agents/{name}.md"


def test_plugin_json_valid():
    data = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert data.get("version"), "plugin.json missing version"


def test_marketplace_entries_exist():
    mk = PLUGIN / "marketplace.json"
    if not mk.is_file():
        pytest.skip("no marketplace.json")
    data = json.loads(mk.read_text(encoding="utf-8"))
    plugins = data.get("plugins", data) if isinstance(data, dict) else data
    for entry in plugins if isinstance(plugins, list) else []:
        src = entry.get("source") or entry.get("path") if isinstance(entry, dict) else None
        if src and not str(src).startswith(("http", "git")):
            assert (REPO / src).exists(), f"marketplace.json entry missing on disk: {src}"


def test_team_skills_excluded_from_standalone():
    cfg = REPO / "scripts" / "standalone_skill_config.py"
    builder = REPO / "scripts" / "build-standalone-skills.sh"
    for f in (cfg, builder):
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            for s in ("marathon", "pr-review-merge"):
                assert s not in text, f"{f.name}: team-only skill '{s}' must not be in the standalone build"
```

- [ ] **Step 2: Run the harness against the current repo — it must pass before any new files are added**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
cd "$WT" && uv run --with pytest pytest -v tests/
```
Expected: all tests PASS. If an existing file trips a check, classify it first:
- **Genuine defect** (real dead link, stray `TODO` outside a code fence) → fix the file, not the test, and note it in the commit message.
- **Legitimate reference the allowlists don't know yet** (a real external skill name, or a built-in `subagent_type`) → add it to `EXTERNAL_SKILLS` or `BUILTIN_AGENTS` in the harness. These allowlists are part of the contract; widening them for a real external name is correct, suppressing a real defect is not.

- [ ] **Step 3: Add a third job to `.github/workflows/tests.yml`**

Append this job under `jobs:` (sibling of `pytest` and `pipeline-tests`):

```yaml
  contract-tests:
    name: plugin contract pytest
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4

      - name: Install uv
        uses: astral-sh/setup-uv@38f3f104447c67c051c4a08e39b64a148898af3a  # v4

      - name: Run pytest
        run: uv run --with pytest pytest -v tests/
```
(No `working-directory` — the harness resolves the repo root from its own path and walks the tree.)

- [ ] **Step 4: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add tests/test_plugin_contract.py .github/workflows/tests.yml
git -C "$WT" commit -m "test: add deterministic plugin contract harness and CI job"
```

---

## Task 1: Scaffold the `pr-review-merge` skill (frontmatter + skeleton)

**Files:**
- Create: `skills/pr-review-merge/SKILL.md`

- [ ] **Step 1: Create the skill file with frontmatter and section skeleton**

Create `skills/pr-review-merge/SKILL.md` with exactly this content:

```markdown
---
name: pr-review-merge
description: >
  Drive a single pull request to merge-ready across all five criteria (sync, CI,
  inline comments, conversation, threads), then smart-merge it. Source-agnostic
  library skill invoked by the /tm, /issues, /fix-pr, and /fix-develop commands and
  by marathon teammates. TRIGGER when a command or agent needs the PR review-to-green
  loop or the smart-merge (stale-bot-CR dismissal, auto-merge criteria, UNSTABLE/UNKNOWN
  handling, merge ordering), or when the user asks to take a PR to green/merge it.
---

# PR Review-to-Green + Smart Merge

Source-agnostic. Consumers pass: PR number, base branch, and bot-reviewer/CI rules
from the project's `## Marathon Configuration` (defaults if absent).

## Ready Criteria (ALL must be true)

## Shell Pitfalls

## Review Loop (each iteration)

## Smart Merge
```

- [ ] **Step 2: Verify frontmatter parses and name matches directory**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
test -f "$WT/skills/pr-review-merge/SKILL.md" && \
awk '/^name:/{print $2}' "$WT/skills/pr-review-merge/SKILL.md"
```
Expected: prints `pr-review-merge` (matches the directory name).

- [ ] **Step 3: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/pr-review-merge/SKILL.md
git -C "$WT" commit -m "feat: scaffold pr-review-merge shared skill"
```

---

## Task 2: Move the Ready Criteria + thread rules + shell pitfalls into `pr-review-merge`

**Files:**
- Modify: `skills/pr-review-merge/SKILL.md`
- Source: `commands/fix-pr.md` lines 7–34 (Ready Criteria, thread rules, Shell Pitfalls), `commands/tm.md` "Mode: Review" lines 316–356

- [ ] **Step 1: Fill "Ready Criteria" section**

Move verbatim into the `## Ready Criteria (ALL must be true)` section the 5-item list and the **Thread resolution rules** block from `commands/fix-pr.md:7–21`. Keep the GraphQL jq-builder mutation exactly. Add this lead sentence before the list:

```markdown
The PR is merge-ready only when all five are simultaneously true. Re-check from the top after every push — a fix can reopen an earlier criterion.
```

- [ ] **Step 2: Fill "Shell Pitfalls" section**

Move verbatim from `commands/fix-pr.md:25–34` the "Never use `gh ... --jq`" rule and the positive-vs-negative-filter block (WRONG/RIGHT examples).

- [ ] **Step 3: Verify the GraphQL mutation survived intact**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'resolveReviewThread' "$WT/skills/pr-review-merge/SKILL.md"
```
Expected: `1` (the jq-builder mutation is present exactly once).

- [ ] **Step 4: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/pr-review-merge/SKILL.md
git -C "$WT" commit -m "feat: move ready criteria, thread rules, shell pitfalls into pr-review-merge"
```

---

## Task 3: Move the Review Loop (base-sync + background CI watcher + conflict patterns) into `pr-review-merge`

**Files:**
- Modify: `skills/pr-review-merge/SKILL.md`
- Source: `commands/fix-pr.md:36–107` (Each Iteration), `commands/tm.md:334–356` (Review Mode iteration)

- [ ] **Step 1: Fill "Review Loop (each iteration)" section**

Move verbatim, in this order:
1. **Step 1: Sync with base branch (FIRST, every iteration)** — the `git fetch origin $BASE && git merge` block + conflict-resolution patterns from `fix-pr.md:38–51`.
2. **Step 2: Check criteria, delegate CI to background agent** — the background-agent `Agent(run_in_background: true, ...)` block + the "while CI runs you are FREE" immediate thread/comment checks from `fix-pr.md:52–91`.
3. **Step 3: Fix and batch** — from `fix-pr.md:93–107`.

Replace any hardcoded `<owner>`/`<repo>` with the note: `Substitute the repo owner/name the consumer passed in.`

- [ ] **Step 2: Verify both the base-sync and background-watcher survived**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'git merge origin' "$WT/skills/pr-review-merge/SKILL.md"
grep -c 'run_in_background: true' "$WT/skills/pr-review-merge/SKILL.md"
```
Expected: each prints `1` or more.

- [ ] **Step 3: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/pr-review-merge/SKILL.md
git -C "$WT" commit -m "feat: move review loop into pr-review-merge"
```

---

## Task 4: Move Smart Merge into `pr-review-merge`

**Files:**
- Modify: `skills/pr-review-merge/SKILL.md`
- Source: `commands/tm.md` "Smart Merge" lines 673–755

- [ ] **Step 1: Fill "Smart Merge" section**

Move verbatim, in order: the "Known: Stale bot CHANGES_REQUESTED" note, Step 1 stale-CR dismissal loop, Step 2 merge-state check, **Auto-Merge Criteria (ALL must be true)**, **UNSTABLE handling**, **UNKNOWN handling**, **Verify before merging**, **After merge**, and **Merge Order (multiple PRs)** from `tm.md:673–755`.

Generalise wording only where it names Task Master: replace `task-master ... set-status --status=done` in the "After merge" block with: `Mark the work unit done via the consumer's close operation (see the calling command's adapter).`

- [ ] **Step 2: Verify the four auto-merge criteria and UNSTABLE/UNKNOWN survived**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'UNSTABLE\|UNKNOWN\|mergeStateStatus' "$WT/skills/pr-review-merge/SKILL.md"
```
Expected: `3` or more.

- [ ] **Step 3: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/pr-review-merge/SKILL.md
git -C "$WT" commit -m "feat: move smart-merge into pr-review-merge"
```

---

## Task 5: Scaffold the `marathon` skill (frontmatter + adapter contract + skeleton)

**Files:**
- Create: `skills/marathon/SKILL.md`

- [ ] **Step 1: Create the skill file with frontmatter, adapter contract, and skeleton**

Create `skills/marathon/SKILL.md` with exactly this content:

```markdown
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

## Step 1: DAG + Hot-File Analysis

## Step 2: Team + Tracking

## Step 3: Spawn Teammates

## Step 4: Lead Monitoring

## Smart Merge

## Crash Recovery

## Completion + Retrospective

## Subagent Fallback (no teams)
```

- [ ] **Step 2: Verify frontmatter and adapter table present**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
awk '/^name:/{print $2}' "$WT/skills/marathon/SKILL.md"
grep -c 'Work-Source Adapter Contract' "$WT/skills/marathon/SKILL.md"
```
Expected: prints `marathon` then `1`.

- [ ] **Step 3: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/marathon/SKILL.md
git -C "$WT" commit -m "feat: scaffold marathon shared skill with adapter contract"
```

---

## Task 6: Move capability detection + DAG/hot-file analysis into `marathon`

**Files:**
- Modify: `skills/marathon/SKILL.md`
- Source: `commands/tm.md` Phase 0 (lines 21–53), Marathon Step 1 (lines 406–459)

- [ ] **Step 1: Fill "Phase 0: Capability Detection"**

Move verbatim the `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` echo, the `$TEAMS_AVAILABLE` rule, and the Marathon Configuration table + defaults from `tm.md:21–53`.

- [ ] **Step 2: Fill "Step 1: DAG + Hot-File Analysis"**

Move verbatim from `tm.md:406–459`: the Global Tag State Rule note (reword its first sentence to be source-neutral: `Never run source-of-truth write commands as parallel background jobs — concurrent writes race.`), the "Analyze dependency tree for maximum concurrency" 9-point list (including hot-file identification and combine-on-shared-file), the report template, and the apply-dependency-changes step.

Replace the enumerate command (`task-master list --json` / `jq` on `tasks.json`) with: `Enumerate work units via the adapter's **enumerate** operation.`

- [ ] **Step 3: Verify the combining heuristic and hot-file rule survived**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -ci 'hot.file' "$WT/skills/marathon/SKILL.md"
grep -ci 'combine' "$WT/skills/marathon/SKILL.md"
```
Expected: each `1` or more.

- [ ] **Step 4: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/marathon/SKILL.md
git -C "$WT" commit -m "feat: move capability detection and DAG analysis into marathon"
```

---

## Task 7: Move team/tracking + teammate spawn into `marathon`

**Files:**
- Modify: `skills/marathon/SKILL.md`
- Source: `commands/tm.md` Step 2 (460–532), Step 3 (534–614)

- [ ] **Step 1: Fill "Step 2: Team + Tracking"**

Move verbatim from `tm.md:460–532`: `TeamCreate`, the `pr-tracking.json` setup, reconciliation steps, tracking structure, CRUD operations, and flaky-check detection. Replace TM status reads (`task-master list --json`) with: `Read unit status via the adapter's enumerate operation.`

- [ ] **Step 2: Fill "Step 3: Spawn Teammates"**

Move verbatim from `tm.md:534–614`: pre-spawn already-merged check, model-selection rules (opus/sonnet/haiku guidance), and the teammate prompt template. **One change to the template:** replace the inline "Review loop — all 5 criteria" workflow (template steps 3–6) with:

```markdown
3. **Review loop:** Use the pr-review-merge skill to drive your PR to green (5 criteria, thread rules, background CI watcher, batch fixes). Do not block on CI yourself.
```

Keep the Setup, Requirements, Architectural Direction, Project Guidelines, Shell Rules, Known Conflict Patterns, Communication, Scope, and Lifecycle sections of the template verbatim. Replace the worktree/setup wording and the "set task in-progress" line with adapter references: `Set the unit in-progress via the adapter; create the worktree using the adapter's branch/worktree convention.`

- [ ] **Step 3: Verify teammate template references pr-review-merge and TeamCreate survived**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'pr-review-merge' "$WT/skills/marathon/SKILL.md"
grep -c 'TeamCreate' "$WT/skills/marathon/SKILL.md"
```
Expected: each `1` or more.

- [ ] **Step 4: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/marathon/SKILL.md
git -C "$WT" commit -m "feat: move team setup and teammate spawn into marathon"
```

---

## Task 8: Move lead monitoring + smart-merge delegation + crash recovery + retro into `marathon`

**Files:**
- Modify: `skills/marathon/SKILL.md`
- Source: `commands/tm.md` Step 4 (616–671), Smart Merge (673–755 → delegate), Ephemeral/Authority/Crash (757–807), Completion (809–866), Subagent Fallback (870–872)

- [ ] **Step 1: Fill "Step 4: Lead Monitoring"**

Move verbatim from `tm.md:616–671`: the team-status report template, the reactive teammate-message table, TOO_COMPLEX handling, lead conflict resolution, non-responsive-teammate escalation, idle≠dead check, unreliable-REVIEW_CLEAR polling loop, lead-overlap rule, accidental-input guard.

- [ ] **Step 2: Fill "Smart Merge" section as a delegation**

Do **not** re-paste the smart-merge body (it now lives in `pr-review-merge`). Write exactly:

```markdown
The lead runs smart-merge via the pr-review-merge skill (Smart Merge section): dismiss stale
bot CRs, verify the four auto-merge criteria, handle UNSTABLE/UNKNOWN, merge in hot-file order.
After a merge, close the unit via the adapter's **close on merge** operation, then proceed to
the wave transition below.
```

Then move verbatim the "After merge" wave-transition list and "If not merge-ready" branches from `tm.md:718–739`, replacing the `task-master set-status --status=done` line with the adapter close reference.

- [ ] **Step 3: Fill "Crash Recovery" and "Completion + Retrospective"**

Move verbatim: Ephemeral Teammates + Lead Authority + Crash Recovery from `tm.md:757–807`, and Completion + Retrospective (Step 6) from `tm.md:809–866`. Keep the PRD-delivery-check wording but generalise its first line to: `Re-read the original work units' acceptance criteria (PRD, issue bodies, or task details) and cross-reference against merged PRs.`

- [ ] **Step 4: Fill "Subagent Fallback (no teams)"**

Move verbatim from `tm.md:870–872`, generalising "after each cleanup cycle" to reference the adapter's enumerate for next-ready units.

- [ ] **Step 5: Verify retro template and crash recovery survived**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'Retrospective\|TeamDelete\|Crash Recovery' "$WT/skills/marathon/SKILL.md"
```
Expected: `3` or more.

- [ ] **Step 6: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add skills/marathon/SKILL.md
git -C "$WT" commit -m "feat: move lead monitoring, crash recovery, retro into marathon"
```

---

## Task 9: Rewire `commands/tm.md` onto the shared skills (TM adapter)

**Files:**
- Modify: `commands/tm.md`

- [ ] **Step 1: Add a TM adapter block near the top (after Phase 0 reference)**

After the "Thin orchestrator" intro, insert:

```markdown
## TM Work-Source Adapter

This command supplies the marathon skill's adapter as:
- **enumerate** — `task-master tags use "<tag>" && task-master list --json`; use `jq` on `tasks.json` for reliable status filtering (`task-master next` can suggest subtask IDs of done parents).
- **mark in-progress** — `task-master set-status --id=<id> --status=in-progress` (run sequentially inline — never as a parallel background job; concurrent TM writes race the global tag).
- **close on merge** — `task-master tags use "<tag>" && task-master set-status --id=<id> --status=done`.
- **branch / worktree** — branch `<tag>--<task-id>--<slug>`; worktree `worktree/<tag>/<task-id>--<slug>`.
```

- [ ] **Step 2: Replace the Marathon Mode: Agent Teams section with a delegation**

Delete `tm.md` lines for "## Marathon Mode: Agent Teams" through the end of its Step 6 (the block ending at the retrospective stats, before "## Marathon Mode: Subagent Fallback"). Replace with:

```markdown
## Marathon Mode: Agent Teams

Prerequisite: `$MARATHON_MODE` AND `$TEAMS_AVAILABLE`.

Use the marathon skill, supplying the TM Work-Source Adapter above and the Marathon
Configuration values from Phase 0. The skill owns DAG/hot-file analysis, team lifecycle,
waves, crash recovery, and the retrospective; it drives each PR via pr-review-merge.
```

- [ ] **Step 3: Replace the Review Mode and Smart Merge bodies with delegation**

In "### Mode: Review (PR open)", delete the inline iteration/criteria body and replace with:

```markdown
Use the pr-review-merge skill to drive PR #<number> to merge-ready (5 criteria, thread
rules, background CI watcher). When all criteria are met, output `<promise>PR_READY</promise>`.
```

Keep the "### Mode: Cleanup" and "### Mode: Implement or Create PR" subagent-dispatch wording, but in the Implement prompt replace the inline "review loop (5 criteria...)" phrase with "use the pr-review-merge skill for the review loop".

- [ ] **Step 4: Replace Subagent Fallback section with a one-line delegation**

```markdown
## Marathon Mode: Subagent Fallback

When `$MARATHON_MODE` but `$TEAMS_AVAILABLE` is `false`, the marathon skill's Subagent
Fallback runs parallel subagents after each cleanup cycle using the TM adapter.
```

- [ ] **Step 5: Verify Planning Mode is untouched and delegations are present**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'Planning Mode\|parse-prd' "$WT/commands/tm.md"        # planning must remain
grep -c 'Use the marathon skill\|pr-review-merge skill' "$WT/commands/tm.md"
```
Expected: planning grep ≥ 2; delegation grep ≥ 2.

- [ ] **Step 6: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add commands/tm.md
git -C "$WT" commit -m "refactor: rewire /tm onto marathon and pr-review-merge skills"
```

---

## Task 10: Diff-review gate for the `/tm` extraction (acceptance gate)

**Files:**
- Read-only: `commands/tm.md` at `HEAD~6..` vs the two skills

- [ ] **Step 1: Assemble the "before" and "after" instruction text**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
# Original marathon+review+merge text, from the spec commit's parent of Task 9
git -C "$WT" show HEAD~1:commands/tm.md > /tmp/tm-before.md   # adjust ref to the pre-rewire tm.md
# After: the skill bodies that now carry that text
cat "$WT/skills/marathon/SKILL.md" "$WT/skills/pr-review-merge/SKILL.md" > /tmp/skills-after.md
```

- [ ] **Step 2: Confirm no orchestration content was lost**

For each of these anchors, confirm it appears in `/tmp/skills-after.md` (the extraction destination):
```bash
for s in "Global Tag State" "hot files" "model selection" "REVIEW_CLEAR" \
         "stale bot CHANGES_REQUESTED" "UNSTABLE" "UNKNOWN" "Crash Recovery" \
         "Retrospective" "background" "resolveReviewThread" "positive jq"; do
  printf '%-32s' "$s"; grep -qi "$s" /tmp/skills-after.md && echo FOUND || echo "MISSING <-- INVESTIGATE"
done
```
Expected: every anchor `FOUND`. Any `MISSING` means content was dropped in extraction — restore it from `/tmp/tm-before.md` before proceeding.

- [ ] **Step 3: Record the gate result (no commit — read-only)**

Note in the PR description that the diff-review passed and list any anchors that needed restoring. The live validation marathon (Task 16) is the behavioural half of this gate.

---

## Task 11: Rewire `commands/fix-pr.md` onto `pr-review-merge`

**Files:**
- Modify: `commands/fix-pr.md`

- [ ] **Step 1: Replace the duplicated body with a delegation**

Replace the "Ready Criteria", "Shell Pitfalls", and "Each Iteration" sections (lines 7–107) with:

```markdown
## What this does

Drive the current branch's PR to merge-ready across all five criteria, then stop for human
review (this command does not auto-merge).

Use the pr-review-merge skill: it owns the 5 ready criteria, thread-resolution rules, shell
pitfalls, base-sync-first, and the background CI watcher. Pass the current PR number
(`gh pr view --json number --jq '.number'`) and the base branch
(`gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'`), plus any bot rules
from the project's Marathon Configuration.
```

Keep the "## Protocol", "## Example Output", and "## Important" sections (the autonomous-loop / max-10-iterations / stop-criteria behaviour is `/fix-pr`-specific and stays).

- [ ] **Step 2: Verify delegation present and duplicate GraphQL removed**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'pr-review-merge skill' "$WT/commands/fix-pr.md"     # expect >=1
grep -c 'resolveReviewThread' "$WT/commands/fix-pr.md"        # expect 0 (now only in the skill)
```
Expected: first `1`, second `0`.

- [ ] **Step 3: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add commands/fix-pr.md
git -C "$WT" commit -m "refactor: rewire /fix-pr onto pr-review-merge skill"
```

---

## Task 12: Rewire `commands/fix-develop.md` onto `pr-review-merge`

**Files:**
- Modify: `commands/fix-develop.md`

- [ ] **Step 1: Replace the Step 4 loop body with a delegation**

Keep Steps 1–3 (default-branch health assessment, diagnosis, worktree+fix creation — these are `/fix-develop`-specific). Replace "## Step 4: Loop Until Green" iteration body (lines 106–135) with:

```markdown
## Step 4: Loop Until Green

Use the pr-review-merge skill to drive the fix PR to merge-ready, passing `$PR` and
`$DEFAULT_BRANCH` and the project's bot rules. It owns the CI/threads/conversation checks
and the positive-jq-filter pitfalls.
```

Keep "## Protocol" and "## Important" (minimal-fixes-only, flaky-handling, max-10-iterations are `/fix-develop`-specific).

- [ ] **Step 2: Verify**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'pr-review-merge skill' "$WT/commands/fix-develop.md"   # expect >=1
grep -c 'Assess Default Branch Health' "$WT/commands/fix-develop.md"  # specific logic kept
```
Expected: both `1` or more.

- [ ] **Step 3: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add commands/fix-develop.md
git -C "$WT" commit -m "refactor: rewire /fix-develop onto pr-review-merge skill"
```

---

## Task 13: Create `commands/issues.md` — triage + GitHub adapter + marathon delegation

**Files:**
- Create: `commands/issues.md`

- [ ] **Step 1: Create the command file**

Create `commands/issues.md` with this content:

````markdown
---
description: GitHub-issue marathon - triage open issues, then run agent-ready ones to merge with Agent Teams
argument-hint: [label-filter] (optional - defaults to all open issues)
---

# GitHub Issue Marathon

> Thin orchestrator. Triages open issues, then delegates execution of `agent-ready` issues
> to the marathon skill (same engine as `/tm`).

## Configuration

Read the repo's CLAUDE.md `## Marathon Configuration` (GitHub Issues subsection) for label
names, with defaults:
- Agent-ready label: `agent-ready`
- Needs-triage label: `needs-triage`
- In-progress label: `in-progress`
- Issue exclude labels: (none)

Also read base branch, required approvals, and bot-reviewer rules (shared with `/tm`).

## Phase 0: Capability Detection

```bash
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS   # set $TEAMS_AVAILABLE (true if "1")
```

## Routing

```bash
ORG=$(gh repo view --json owner --jq '.owner.login')
REPO=$(gh repo view --json name --jq '.name')
READY=$(gh issue list --label "agent-ready" --state open --json number --jq 'length')
```

- `READY > 0` → **Marathon mode.** Work ONLY the `agent-ready` issues. Do NOT assess or
  modify untagged issues — the human has curated the queue by tagging.
- `READY == 0` → **Triage mode** (below).

## Triage Mode (no agent-ready issues exist)

Enumerate open issues minus the exclude labels:
```bash
gh issue list --state open --json number,title,body,labels | \
  jq '[.[] | select((.labels[].name) as $l | ($l | IN("<exclude-labels>")) | not)]'
```

For each issue, assess whether it is actionable as-is (clear scope, acceptance criteria
inferable, no open question):
- **Clear enough** → add the agent-ready label:
  `gh issue edit <N> --add-label "agent-ready"`
- **Ambiguous** → post clarifying questions as a comment, then label needs-triage:
  ```bash
  gh issue comment <N> --body "$(cat <<'EOF'
  Triage questions before this can be picked up by an agent:
  1. <question>
  2. <question>
  EOF
  )"
  gh issue edit <N> --add-label "needs-triage"
  ```

Then **report and STOP** (mirrors `/tm` planning):
```
## Issue Triage: <org>/<repo>

Tagged agent-ready: #12, #15, #18
Tagged needs-triage (questions posted): #20, #21

OK to start on the agent-ready issues? Re-run /issues to begin, or reply to proceed.
```

Do NOT spawn teammates in triage mode.

## Marathon Mode (agent-ready issues exist)

### GitHub Work-Source Adapter

Supply the marathon skill's adapter as:
- **enumerate** — `gh issue list --label "agent-ready" --state open --json number,title,body,labels`;
  dependencies from `gh api repos/$ORG/$REPO/issues/<N>/dependencies/blocked_by`
  (each blocker issue number is a dependency edge). Complexity: infer from issue body/labels.
- **mark in-progress** — `gh issue edit <N> --add-label "in-progress"`.
- **close on merge** — the teammate's PR body includes `Closes #<N>` (and `Closes #<M>` for
  every combined issue); GitHub auto-closes on merge. After merge, verify with
  `gh issue view <N> --json state --jq '.state'` == `CLOSED`.
- **branch / worktree** — branch `issue-<N>--<slug>`; worktree `worktree/issues/<N>--<slug>`.
  For a combined group, use the lowest issue number: `issue-<N>--<slug>`.

### Run

Use the marathon skill with the GitHub Work-Source Adapter above and the Marathon
Configuration values. The skill builds the DAG from native `blocked_by` deps plus hot-file
combining, spawns one teammate per issue or combined group, drives each PR via
pr-review-merge, and smart-merges in waves. Combined-issue teammates put `Closes #N` for
every issue they resolve in the PR body.

## Orchestrator Flow

```
/issues [label-filter] → detect teams → check agent-ready count → route:
  ├─ agent-ready exist → MARATHON (marathon skill, GitHub adapter)
  └─ none exist        → TRIAGE (tag agent-ready / post questions+needs-triage) → report → STOP
```
````

- [ ] **Step 2: Verify frontmatter, routing, and both skill references present**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'description:' "$WT/commands/issues.md"          # frontmatter
grep -c 'marathon skill' "$WT/commands/issues.md"        # delegates to engine
grep -c 'Closes #' "$WT/commands/issues.md"              # close-on-merge
grep -c 'blocked_by' "$WT/commands/issues.md"            # native DAG
```
Expected: each `1` or more.

- [ ] **Step 3: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add commands/issues.md
git -C "$WT" commit -m "feat: add /issues GitHub-issue marathon command"
```

---

## Task 14: Add the GitHub Issues subsection to the marathon-config example

**Files:**
- Modify: `commands/tm-marathon-config-example.md`

- [ ] **Step 1: Append a GitHub Issues subsection inside the `## Marathon Configuration` block**

Add after the existing "### CI Patterns" section:

```markdown
### GitHub Issues (for `/issues`)

<!-- Defaults shown. Adjust label names to match your repo's conventions. -->

- **Agent-ready label**: `agent-ready` (opt-in label that makes an issue marathon-eligible)
- **Needs-triage label**: `needs-triage` (applied with a clarifying-question comment)
- **In-progress label**: `in-progress` (applied when a teammate starts an issue)
- **Issue exclude labels**: (none — e.g. `discussion`, `wontfix`, `question` to skip during triage)
```

- [ ] **Step 2: Update the intro line so it covers both commands**

Change the file's intro sentence that says "The `/tm` command reads this section" to: "The `/tm` and `/issues` commands read this section to configure marathon mode for your specific codebase."

- [ ] **Step 3: Verify**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -c 'GitHub Issues (for `/issues`)\|agent-ready' "$WT/commands/tm-marathon-config-example.md"
```
Expected: `2` or more.

- [ ] **Step 4: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add commands/tm-marathon-config-example.md
git -C "$WT" commit -m "docs: add GitHub Issues subsection to marathon config example"
```

---

## Task 15: Plugin invariants, version bump, standalone-build exclusion, README

**Files:**
- Modify: `.claude-plugin/plugin.json`, `README.md`

- [ ] **Step 1: Confirm the two new skills have valid SKILL.md (plugin invariant)**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
for s in marathon pr-review-merge; do
  f="$WT/skills/$s/SKILL.md"
  test -f "$f" && head -1 "$f" | grep -q '^---' && \
    awk -v n="$s" '/^name:/{ok=($2==n)} END{exit !ok}' "$f" && echo "$s OK" || echo "$s INVALID"
done
```
Expected: `marathon OK` and `pr-review-merge OK`.

- [ ] **Step 2: Confirm the new skills are NOT in the standalone build list**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
grep -n 'marathon\|pr-review-merge' "$WT/scripts/build-standalone-skills.sh" "$WT/scripts/standalone_skill_config.py" 2>/dev/null || echo "NOT REFERENCED (correct)"
```
Expected: `NOT REFERENCED (correct)` — they depend on Agent Teams and must not ship in the chat ZIP. If they appear, they were added by mistake; remove them.

- [ ] **Step 3: Bump the plugin version (MINOR)**

Read the current version, increment the MINOR component, reset PATCH to 0:
```bash
WT=$(git rev-parse --show-toplevel)
jq -r '.version' "$WT/.claude-plugin/plugin.json"   # e.g. 1.13.1
```
Edit `.claude-plugin/plugin.json` `.version` to the next minor (e.g. `1.13.1` → `1.14.0`). Update the `description` field to mention `/issues` and the shared marathon engine.

- [ ] **Step 4: Document `/issues` and the shared skills in README**

Add a `/issues` entry alongside the existing `/tm` documentation in `README.md`, and a short note that `/tm`, `/issues`, `/fix-pr`, `/fix-develop` now share the `marathon` and `pr-review-merge` skills. Match the README's existing section style.

- [ ] **Step 5: Verify version moved and JSON is valid**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
jq -e '.version' "$WT/.claude-plugin/plugin.json" && echo "JSON VALID"
```
Expected: prints the new version then `JSON VALID`.

- [ ] **Step 6: Commit**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" add .claude-plugin/plugin.json README.md
git -C "$WT" commit -m "feat: bump version and document /issues + shared marathon skills"
```

---

## Task 16: Validation gate — assess suite, dry behavioural check, PR

**Files:**
- None modified (verification + PR)

- [ ] **Step 1: Run all three pytest suites green**

Run:
```bash
WT=$(git rev-parse --show-toplevel)
cd "$WT" && uv run --with pytest pytest -v tests/          # contract harness (this feature)
cd "$WT/skills/assess" && uv run --with pytest pytest -q   # untouched by this work
cd "$WT/scripts" && uv run --with pytest pytest -q         # untouched by this work
```
Expected: all green. The contract harness is the deterministic acceptance check for the new skills/commands (frontmatter, references, placeholders, standalone-exclusion). The assess/scripts suites touch no Python here, so any red there is unrelated — investigate before proceeding. (Per the assess-tests memory, neutralize global git hooks if phantom git-commit failures appear.)

- [ ] **Step 2: Dry behavioural check of `/tm` delegation**

Read `commands/tm.md` end-to-end and confirm: Planning Mode is intact and self-contained; Marathon/Review/Smart-Merge now delegate to the skills; the TM adapter block defines all four operations. Confirm `skills/marathon/SKILL.md` references `pr-review-merge` in both the teammate template and the lead smart-merge step.

- [ ] **Step 3: Open the PR**

```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" push -u origin marathon-skill-extraction
gh pr create --base main \
  --title "feat: add /issues marathon and extract shared marathon + pr-review-merge skills" \
  --body "Implements docs/superpowers/specs/2026-05-29-issues-marathon-shared-skills-design.md. Extracts the marathon engine and PR review/merge logic from tm.md into two shared skills; rewires /tm, /fix-pr, /fix-develop; adds /issues. Diff-review gate (Task 10) passed. Behavioural acceptance: a live validation marathon on a real tag with /tm post-refactor, plus a first /issues run against a repo with agent-ready issues."
```

- [ ] **Step 4: Behavioural acceptance (post-merge-readiness, with the user)**

The spec's acceptance gate is a **live validation marathon**: run `/tm <tag>` on a real tag after the refactor and confirm identical behaviour (DAG analysis → waves → smart-merge → retro), then a first `/issues` run against a repo with `agent-ready` issues. Coordinate with the user to schedule this — it is the behavioural half of the Task 10 gate and cannot be faked with structural checks.

---

## Self-Review (completed during planning)

- **Spec coverage:** two shared skills (Tasks 1–8), `/tm` refactor (9–10), `/fix-pr` + `/fix-develop` (11–12), `/issues` triage+marathon+adapter+DAG+combined-PRs (13), config (14), version/standalone-exclusion/README (15), validation gate incl. live marathon (16). All spec sections mapped.
- **Adapter consistency:** the four operations (enumerate / mark in-progress / close on merge / branch+worktree) are named identically in the marathon contract (Task 5), the TM adapter (Task 9), and the GitHub adapter (Task 13).
- **Skill-name consistency:** `marathon` and `pr-review-merge` referenced by those exact slugs throughout; directory names match frontmatter `name:` (Tasks 1, 5, 15).
- **No silent loss:** Task 10 anchor-grep gate guards the verbatim extraction against dropped content; the Task 0 contract harness guards frontmatter/references/standalone-exclusion on every PR thereafter.
- **Harness ordering:** Task 0 builds the harness and goes green against the current repo before any new file exists; the `Use the X skill` and standalone-exclusion checks then fail-closed as Tasks 2–8 add the new skills and Tasks 9–13 add the references.
