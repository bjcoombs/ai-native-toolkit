# Claude Config

Personal Claude Code configuration, customizations, and workflow tools.

> **Heads up — not portable.** This config is tuned to one author's daily setup: a `<repo>-main/` + `worktree/` directory layout, GitHub + `gh` CLI, Task Master, CodeRabbit/claude[bot] review threads, and the Agent Teams capability flag. The opinionated framework agents (`/huddle`, `/6hats`, `/assess`, `/understand`) are reasonably portable; the workflow commands (`/tm`, `/fix-pr`, `/fix-develop`) bake in those assumptions and may need editing before they work for you. See [Git Workflow](#git-workflow) and [Adapting for Your Workflow](#adapting-for-your-workflow) below.

## Contents

### Commands

| Command | Description |
|---------|-------------|
| `/tm` | Task Master orchestration — context-aware: starts, reviews, or cleans up tasks based on current state |
| `/tm-marathon-config-example` | Reference configuration block to drop into a project's `CLAUDE.md` for marathon-mode `/tm` |
| `/huddle` | Multi-perspective analysis with persistent professional lenses cycling through Six Hats phases |
| `/6hats` | Solo Six Hats analysis — alias for `/huddle` with team size 1 |
| `/assess` | Layered codebase assessment for AI-agent contributor readiness |
| `/understand` | Deep understanding mode (nemawashi) — exhaustive context-gathering before action |
| `/fix-pr` | Autonomous PR fixing loop — iterates on CI failures and review comments until green |
| `/fix-develop` | Autonomous fix loop for failing CI on the repo's default branch |

The `/tm` command detects context automatically:
- **Not in worktree** → Start mode (begin next task or specified task)
- **In TM worktree** → Detects state and runs appropriate action (implement, create PR, review, cleanup)

### Six Thinking Hats Framework

A blind spot detector for high-stakes decisions, based on Edward de Bono's Six Thinking Hats method. Use `/6hats` for a quick parallel analysis or `/huddle` for a multi-perspective deliberation.

```text
/6hats Should we rewrite our monolith in microservices?
/huddle Should we migrate our monolith to microservices?
```

| Agent | Role |
|-------|------|
| `white-hat` | Facts and evidence |
| `red-hat` | Gut feelings and emotional drivers |
| `black-hat` | Risks and critical analysis |
| `yellow-hat` | Benefits and opportunities |
| `green-hat` | Creative alternatives |
| `blue-hat` | Synthesis and recommendation |
| `scribe` | Structures hat output into actionable documentation (invoke directly via `Task(subagent_type="scribe")`) |

#### The Trade-off: Tokens vs Blind Spots

**Cost:** 5-10x the tokens of a single prompt. Six parallel agents plus synthesis adds up.

**Benefit:** Catches the question you didn't know to ask. Black Hat might reveal your "performance optimization" is really about deployment fear. Green Hat might find the lazy solution that actually works.

**Real example:** This very README section was reviewed via `/6hats review please`. Black Hat called out that the original version was "a 13-point solution to a 2-point problem" with "rigged comparisons" and "zero evidence." That critique led to this rewrite. A single prompt wouldn't have been that harsh.

#### When to Use It

- Architecture decisions you can't easily reverse
- "Should we..." questions where you suspect you're asking the wrong question
- Decisions where being wrong costs 100x more than the analysis
- When you want pushback, not validation

#### When to Skip It

For routine decisions, a single well-crafted prompt is enough:

```
Help me decide [X]. Be opinionated. If this is a bad idea, say so directly.
What am I not considering? What's the lazy solution that might work?
```

Use Six Hats when the stakes justify the token cost. Skip it for debugging, implementation details, or decisions you can easily reverse.

## Repository Structure

```
claude-config/
├── README.md
├── CLAUDE.md                          # Personal guidelines and instructions
├── .claude-plugin/
│   └── plugin.json                    # Plugin manifest (enables /plugin add)
├── skills/
│   ├── assess/
│   │   ├── SKILL.md                   # Codebase readiness assessment + complexity hotspot
│   │   └── scripts/
│   │       └── complexity-treemap.py  # Codecov-style hotspot SVG generator
│   └── huddle/
│       └── SKILL.md                   # Multi-lens Six Hats deliberation
├── commands/
│   ├── tm.md                          # Task Master unified command
│   ├── tm-marathon-config-example.md  # CLAUDE.md snippet for marathon mode
│   ├── 6hats.md                       # Solo Six Hats (alias for huddle)
│   ├── understand.md                  # Nemawashi context-gathering
│   ├── fix-pr.md                      # Autonomous PR fix loop
│   └── fix-develop.md                 # Autonomous default-branch fix loop
└── agents/
    ├── white-hat.md
    ├── red-hat.md
    ├── black-hat.md
    ├── yellow-hat.md
    ├── green-hat.md
    ├── blue-hat.md
    └── scribe.md                      # Structures hat output into docs
```

### Skills vs commands

Skills (`skills/<name>/SKILL.md`) carry an instruction file *plus* bundled assets (scripts, templates). The Claude Code runtime auto-discovers them and the agent invokes the relevant skill when its `description` matches the user's request. `/assess` ships `complexity-treemap.py` alongside its SKILL.md so the agent can run the treemap without external setup.

Commands (`commands/<name>.md`) are slash-only prompts with no bundled assets — kept for workflows that don't need supporting scripts.

## Installation

### As a plugin (recommended)

Install via Claude Code's plugin manager — gives you the skills, commands, and agents in a namespaced bundle that doesn't touch your existing `~/.claude/` files:

```
/plugin add https://github.com/bjcoombs/claude-config
```

Skills are namespaced under the plugin: `/claude-config:assess`, `/claude-config:huddle`, etc. Update with `/plugin update claude-config`, remove with `/plugin remove claude-config`.

### As a full `~/.claude/` clone (alternative)

If you want this repo to *be* your entire Claude Code config, clone it into `~/.claude/`:

```bash
git clone git@github.com:bjcoombs/claude-config.git ~/.claude/
```

If `~/.claude/` already exists and you only want the skills/commands/agents:

```bash
git clone git@github.com:bjcoombs/claude-config.git /tmp/claude-config
cp -r /tmp/claude-config/skills   ~/.claude/
cp -r /tmp/claude-config/agents   ~/.claude/
cp -r /tmp/claude-config/commands ~/.claude/
```

## Git Workflow

This setup assumes a specific directory structure using git worktrees. The key principle: **never work directly on the default branch**.

### Directory Structure

```
~/dev/github.com/<org>/<repo>/
├── <repo>-main/                    # SACRED - always on default branch, never modified
└── worktree/
    ├── <tag>/                      # Task Master tag folder (nested)
    │   ├── 1--create-schema/       # Task worktree
    │   └── 2--add-api/             # Another task worktree
    └── fix-login-bug/              # Non-TM worktree (flat)
```

### Why This Structure?

1. **`<repo>-main/` is sacred**: Always stays on `develop`/`main`, always clean. This gives you a pristine reference point and makes creating new branches reliable.

2. **All work in worktrees**: Every task gets its own worktree. No branch switching, no stashing, no conflicts between tasks.

3. **Parallel work**: Multiple worktrees = multiple tasks in progress simultaneously in different terminal windows.

### Creating a Worktree

```bash
# Start from the sacred directory
cd ~/dev/github.com/<org>/<repo>/<repo>-main
git checkout develop && git pull origin develop

# Create branch and worktree
git branch fix-login-bug
git worktree add ../worktree/fix-login-bug fix-login-bug

# Work there
cd ../worktree/fix-login-bug
# ... make changes, commit, push, create PR ...

# Cleanup after PR merges
cd ~/dev/github.com/<org>/<repo>/<repo>-main
git worktree remove ../worktree/fix-login-bug
git branch -d fix-login-bug
```

### Task Master Worktrees

When using Task Master, worktrees are nested by tag:

```bash
# Task Master creates this structure automatically via /tm
worktree/458-feature/1.1--create-schema
worktree/458-feature/1.2--add-validation
worktree/458-feature/2--write-tests
```

The `/tm` command handles worktree creation and cleanup automatically based on task state.

### Adapting for Your Workflow

The framework agents (`/huddle`, `/6hats`, `/assess`, `/understand`) are reusable as-is. The workflow commands embed assumptions you will likely need to override:

- **Directory layout** — `commands/tm.md`, `commands/fix-pr.md`, `commands/fix-develop.md` all assume `~/dev/github.com/<org>/<repo>/<repo>-main/` + sibling `worktree/`. Edit the path patterns to match your structure.
- **Default branch** — `/fix-develop` derives the branch via `gh repo view --json defaultBranchRef`. `/tm` uses a `$BASE_BRANCH` variable. Other commands may still reference `develop` in prose; check before relying on them on a `main`-default repo.
- **Required external tools** — `gh` CLI for GitHub, Task Master for `/tm`, optional Agent Teams capability flag (`$CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) for `/tm` marathon mode.
- **Review-bot conventions** — PR-loop logic in `/tm`, `/fix-pr`, `/fix-develop` distinguishes CodeRabbit, claude[bot], and human threads. Adjust if your repo uses different bots.
- **CLAUDE.md** — your global / project `CLAUDE.md` references to the directory structure need to match.

## License

The agent implementations are provided as-is for use with Claude Code.

- The Six Thinking Hats method is the intellectual property of Edward de Bono.
- The Task Master commands are designed for use with [Claude Task Master](https://github.com/eyaltoledano/claude-task-master) by Eyal Toledano.
