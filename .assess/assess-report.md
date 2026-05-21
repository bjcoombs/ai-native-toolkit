# Codebase Assessment: claude-config (ai-native-toolkit plugin)

_Generated 2026-05-21._

## How to read this report

This is an improvement roadmap, not a verdict. It pairs two views:

- **Where the codebase is today** — the hotspot SVG shows current complexity and churn at a glance. Vivid red = complex AND actively changing = the files most likely to bite an agent (or a human) next week.
- **What scaffolding is in place to keep it from getting worse** — the 7-layer AI Readiness score measures whether the system enforces contracts that catch the issues the hotspots reveal.

A codebase can be 7/7 and still on fire (great scaffolding, legacy debt) — or 2/7 with a calm treemap (small codebase, no enforcement needed yet). The pair matters.

> **Special note on this repo:** `claude-config` is the source repository for the `ai-native-toolkit` Claude Code plugin. Its deliverable is primarily markdown breadcrumbs (`agents/`, `commands/`, `skills/`) that *other* repos consume, plus one Python script (`skills/assess/scripts/complexity-treemap.py`) that runs at skill invocation time. There is no traditional application code. Layer 5 (Coverage Gates) doesn't meaningfully apply; Layers 1-3 apply only to the single Python file and to the structural shape of the markdown. The "Top 3 Actions" are tailored to what a plugin repo of this kind actually needs.

## Hotspot snapshot

![Complexity hotspot](./complexity-heatmap.svg)

- **Files scored:** 1 (out of 22 total files; the other 21 are markdown, JSON, or license text)
- **Churn window chosen:** all-time (the script clamped to all-time because recent windows had no activity at the time of scoring — every file in this repo has a tiny commit history)
- **Complexity profile:** p95 ccn 117 (max 117); p95 LOC 453 (max 453) — these are degenerate (n=1) but the raw numbers matter
- **Top hotspot:**
  1. `skills/assess/scripts/complexity-treemap.py` — 453 LOC, ccn **117**, 1 commit in window

The single scoreable file in this repo has a file-aggregate cyclomatic complexity of 117 — that's the sum of per-function CCN across the script. With 453 LOC, average per-function CCN is roughly in the single digits, so it's not necessarily one deeply nested mess. Still, this is the script that *generates* this very report — there is a delicious meta-quality to the fact that the highest-complexity file in the toolkit-for-assessing-complexity is the complexity-assessor itself. Worth a docstring breakdown and a function-level CCN cap rule the next time you touch it.

## AI Readiness

**Score: 2 / 7** — Basic

| Layer | Status | Evidence | Gap |
|-------|--------|----------|-----|
| 0: Breadcrumbs | Partial | `README.md` is thorough (170 lines): commands, agents, git workflow, install steps. Every `agents/*.md`, `commands/*.md`, and `skills/**/SKILL.md` uses YAML frontmatter with consistent keys. `.claude-plugin/plugin.json` and `marketplace.json` are well-formed. | No `CLAUDE.md` / `AGENTS.md` at repo root. An agent asked to add a new skill or modify a command has zero in-repo guidance on frontmatter contracts, voice, naming, or banned patterns. The author's global `~/.claude/CLAUDE.md` does not travel with the repo. |
| 1: Code Design | Partial | Only one Python file. Uses PEP 723 inline metadata (modern, lockfile-free). No top-level globals beyond constants; functions are typed in spots. | `complexity-treemap.py` (the sole code file) has aggregate CCN 117 across 453 LOC. No `from __future__ import annotations`, partial type hints, no docstrings on most functions. The repo's "code design" surface is small, but what's there isn't ratcheted. |
| 2: Linters | Missing | No `.markdownlint*`, no `pyproject.toml`/`ruff.toml`/`.ruff.toml`, no `.prettierrc`, no `.editorconfig`. | Markdown drift unenforced; Python script unlinted. Frontmatter typos (e.g., `model: inhret`) and Python style regressions both ship silently. |
| 3: Architecture Tests | Missing | No frontmatter validator, no script that asserts every file in `agents/` has the required keys, no check that commands reference existing agents, no test that plugin.json/marketplace.json stay consistent. | A renamed agent file, a missing `description:` in frontmatter, or a marketplace.json that points at a non-existent skill all ship silently. |
| 4: CI Pipeline | Missing | No `.github/workflows/`. The repo has been releasing a plugin to a marketplace with zero automated validation on push or PR. | Every change goes straight to `main` with no safety net. A broken `plugin.json` would only be caught when someone tries to install the plugin. |
| 5: Coverage Gates | N/A | Only one Python file, no tests. Coverage isn't a meaningful metric at this scale. | Doesn't apply — but see Action 2 about adding a minimum smoke test. |
| 6: Code Review Bots | Missing | No `.coderabbit.yaml`, no Copilot review config, no bot activity visible. | Solo-maintained plugin with no second-pair-of-eyes on PRs. PR #8 ("Use string for repository field, not object") suggests bugs do reach `main` — exactly the class of thing a review bot would have caught. |
| 7: AI Project Mgmt | Missing | No `.taskmaster/`, no `retro/`, no structured backlog inside this repo. The `tm.md` command exists and is shipped *by* this plugin but isn't *used on* this plugin. | The repo ships AI orchestration tooling but doesn't dogfood it on itself. Any drift between the design (in `tm.md`) and reality (how TM actually gets used day-to-day) is invisible. |

### Maturity Level

| Score | Level | Description |
|-------|-------|-------------|
| 0-1 | Not Ready | Agent will produce inconsistent, unvalidated code |
| 2-3 | Basic | Norms exist but aren't enforced. Agent works but drifts |
| 4-5 | Solid | Contracts catch most issues. Agent is productive |
| 6-7 | AI-Native | System self-improves. Agents work reliably at scale |

A score of 2 is honest. The norms are clearly present in the author's head — every file in the repo respects them — but nothing in the repo enforces them. For a solo-maintained plugin this hasn't bitten yet, but the moment external contributors arrive or an agent attempts to add a new skill autonomously, the gap matters.

## Top 3 Actions

Prioritize by leverage: lock in the structural contract first, then automate validation, then make the feedback loop visible. Each action is sized to a single session.

| # | Action | Layer | Effort | Command / First Step | Hotspot files this addresses |
|---|--------|-------|--------|---------------------|------------------------------|
| 1 | Add a root `CLAUDE.md` (and a sibling `AGENTS.md` symlink for cross-tool compatibility) with repo-local conventions. Required frontmatter keys: `agents/*.md` → `name`, `description`, `model`, `color`. `commands/*.md` → `description`, `argument-hint`. `skills/**/SKILL.md` → `name`, `description`. Voice rules: imperative, terse, no second-person fluff, no em-dashes. Convention: every command in `commands/` either invokes an agent in `agents/` or is explicitly orchestrator-only. Convention: every skill listed in `.claude-plugin/marketplace.json` must exist on disk. | 0 | Small | Create `CLAUDE.md` next to `README.md`; cross-link from `README.md`. | — |
| 2 | Add `.github/workflows/validate.yml` running three jobs on every PR: (a) `markdownlint-cli2` with a relaxed `.markdownlint.json` (line length off, ATX headers, no trailing whitespace); (b) a ~20-line Python validator that loads every `agents/*.md`, `commands/*.md`, and `skills/**/SKILL.md` with PyYAML, asserts required frontmatter keys per the contract in Action 1, and asserts every skill in `marketplace.json` exists; (c) `ruff check skills/assess/scripts/`. Plus `.ruff.toml` enabling `E`, `F`, `B`, `UP`, `C901` (cyclomatic complexity, threshold 15). | 2 + 3 + 4 | Small | One workflow file; one `scripts/validate-frontmatter.py`; one `.markdownlint.json`; one `.ruff.toml`. Triggers on PR and push to `main`. | `skills/assess/scripts/complexity-treemap.py` — `C901` at threshold 15 will immediately flag whichever functions in this file blow the cap, giving you a targeted refactor list. The file-aggregate CCN of 117 implies at least one or two functions are doing too much. |
| 3 | Dogfood Task Master on this repo. `task-master init` in the worktree; create a `plugin-roadmap` tag; seed 3-5 backlog items (e.g., "add brainstorming skill", "add review bot", "split complexity-treemap.py into modules"). Add a `retro/` directory with one short markdown per noteworthy plugin change, so future-you and future agents can trace *why* a rule exists. This closes Layer 7 and creates the feedback loop the toolkit's own `/huddle` and `/tm` commands assume. | 7 | Medium | `task-master init`; commit `.taskmaster/`; create `retro/README.md` describing the convention. | — |

### Why these three?

Action 1 is the highest-leverage move — a single `CLAUDE.md` converts implicit conventions (which clearly exist; the codebase is consistent) into an explicit contract any future agent can follow when extending the plugin. Action 2 closes the loop: without CI, Action 1 is honour-system. The Python complexity rule specifically targets the one real hotspot the treemap surfaced. Action 3 is the long game — a toolkit that ships AI project-management primitives but doesn't use them on itself has no feedback channel for drift between design and lived reality.

## Additional Opportunities

- **Add a `LICENSE` reference in `README.md`.** The repo has `LICENSE` (Apache-2.0) but the README's License section doesn't say "Apache-2.0" — it says "as-is for use with Claude Code", which is now incorrect.
- **Add a `CONTRIBUTING.md`** if external PRs are welcome. A plugin repo's review heuristics differ from a code repo's (prompt-injection risk, model-version assumptions, voice consistency).
- **Add a CodeRabbit or Copilot review config** — for a solo-maintained plugin, a review bot is the cheapest second pair of eyes available, and PR #8's repo-field-shape regression is the exact class of thing it catches.
- **Pin `markdownlint-cli2` and `ruff` versions** in the workflow so rules don't shift under you when GitHub-hosted runners auto-update.
- **Break up `complexity-treemap.py`** if Action 2's CCN rule lights up. The script does file discovery, lizard/scc invocation, churn computation, percentile math, and SVG rendering — at least three of those are separable modules.

## Strengths

- **`README.md` is genuinely good.** 170 lines covering purpose, contents, install, git workflow, and license. Includes a meta-anecdote ("this very section was reviewed via `/6hats`") that demonstrates the tool — unusual self-awareness for a personal config repo.
- **Frontmatter discipline is already there in practice.** Every agent, command, and skill file inspected has a clean YAML frontmatter block with consistent keys. The convention exists — it just isn't enforced.
- **Clear separation between agents, commands, and skills.** The directory structure communicates intent without needing docs. `.claude-plugin/marketplace.json` makes the public surface explicit.
- **Modern Python.** The single Python script uses PEP 723 inline metadata — no `requirements.txt`, no lockfile to drift, `uv` resolves dependencies on demand. Good 2026 choice.
- **The toolkit is ambitious and self-referential.** The plugin ships `/huddle`, `/6hats`, `/assess`, and `/tm` — orchestration, deliberation, assessment, and task management. A maintainer who builds this kind of tooling has the taste to close the remaining gaps quickly.

---

_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit). Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
