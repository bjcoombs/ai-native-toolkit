# AI Native Toolkit

A Claude Code plugin: skills, agents, and commands for AI-native development. Runs locally in your Claude Code session, against your own codebase, using whichever model you're already paying for - nothing leaves your machine beyond what Claude Code itself sends.

The headline pieces are two **skills**:

- **`/assess`** - score any codebase's readiness for AI agent contributors against a 7-layer contract model, with a Codecov-style complexity hotspot SVG. Generates a report + treemap and opens a PR in the target repo.
- **`/huddle`** - structured multi-perspective deliberation using Six Thinking Hats with Fibonacci team sizing (solo -> debate -> huddle -> panel -> board).

## What `/assess` produces

[![Example complexity hotspot from a real codebase](docs/example-heatmap.svg)](docs/example-heatmap.svg)

> Real output from `/ai-native-toolkit:assess` run against a ~150k-LOC private monorepo (file paths sanitized). Size encodes lines of code, hue encodes cyclomatic complexity (red = high), saturation encodes recent git churn (vivid = active). Vivid red blocks are the migration risk an agent (or human) is most likely to break next week. Hover any block for its LOC, CCN, and recent commit count.

Alongside the SVG you get an `assess-report.md`: a 0-7 layered readiness score (breadcrumbs, type safety, linters, architecture tests, CI, coverage, review bots, AI project management), three concrete leverage actions naming specific files, and a stats sidecar (`complexity-stats.json`) with percentiles and ranked hotspot lists.

**Build artifacts and generated code are filtered by default** so a single `main.dart.js` or thousands of lines of `.pb.go` don't dominate the treemap. The filter catches minified bundles (`*.min.js`, `*.bundle.js`, `main.dart.js`), sourcemaps, protobuf bindings (`*.pb.go`, `*.connect.go`, `*_pb.ts`), Go generators (`wire_gen.go`, `zz_generated_*.go`), .NET source generators (`*.designer.cs`, `*.g.cs`), Dart codegen (`*.freezed.dart`, `*.g.dart`), and files under build dirs (`dist/`, `build/`, `.next/`, `.nuxt/`, `node_modules/`, etc.). Pass `--include-artifacts` to disable. If a single file still holds >30% of total LOC after filtering, the script warns - usually that's a build artifact that needs `.gitignore`.

The skill runs locally - lizard, optional scc, and git log do the analysis in your Claude Code session. No data leaves the machine.

> **Portability split.** The framework pieces (`/assess`, `/huddle`, `/6hats`, `/understand` and their agents) are portable and work in any Claude Code session. The workflow commands (`/tm`, `/fix-pr`, `/fix-develop`) bake in one author's daily setup: a `<repo>-main/` + `worktree/` layout, GitHub + `gh` CLI, Task Master, CodeRabbit/claude[bot] review threads, and the Agent Teams capability flag. See [Adapting](#adapting-for-your-workflow) before relying on them in a different setup.

## Install

From inside a Claude Code session (not a shell - `/plugin` is a Claude Code command):

```text
/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit
/plugin install ai-native-toolkit@ai-native-toolkit
```

Skills appear namespaced: `/ai-native-toolkit:assess`, `/ai-native-toolkit:huddle`. Update with `/plugin update ai-native-toolkit`. Remove with `/plugin remove ai-native-toolkit`. The plugin doesn't touch your existing `~/.claude/` files.

## Also available in Claude Desktop chat and Cowork

`/assess` and `/huddle` are available as standalone skill ZIPs — no Claude Code session or plugin install required. Works in Claude Desktop chat, claude.ai web, and Cowork.

**Install:**
1. Download `assess.zip` and `huddle.zip` from the [standalone-skills-latest release](https://github.com/bjcoombs/ai-native-toolkit/releases/tag/standalone-skills-latest)
2. In Claude Desktop or claude.ai: **Settings → Customize → Skills → Upload Skill**
3. Upload each ZIP and toggle the skill on

In this context `/huddle` runs in solo or phased sub-agent mode — Claude reasons through each hat phase directly. Team mode (persistent agents with cross-talk) requires Claude Code. `/assess` runs the full layer assessment; the SVG treemap and deterministic wiki require terminal access to the bundled scripts.

After installing, verify trigger descriptions work as expected: try "how AI-ready is this codebase?" for assess and "run a huddle on this decision" for huddle in a fresh chat. If a skill doesn't auto-activate, check that it's toggled on and update `standalone_description` in `scripts/standalone_skill_config.py` if the phrasing misses.

## Try it

### Assess a codebase

```text
/ai-native-toolkit:assess
```

Runs against the current directory (or pass a path). Produces:

- `.assess/assess-report.md` - layered score, top 3 leverage actions, hotspot callouts
- `.assess/complexity-heatmap.svg` - the treemap shown above
- `.assess/complexity-stats.json` - percentiles and ranked file lists that feed the report

After writing, the skill asks whether to open a PR in the target repo with both files.

### Run a huddle on a hard decision

```text
/ai-native-toolkit:huddle Should we migrate the monolith to microservices?
```

Spawns a Fibonacci-sized team (default 3) that cycles through De Bono's six hats - facts, feelings, risks, benefits, alternatives, synthesis - and returns a structured recommendation. Use `/6hats <q>` for a faster solo variant.

## What's in the box

### Skills (auto-discovered by Claude Code)

| Skill | Description |
|-------|-------------|
| `/assess` | Layered AI-readiness assessment (0-7 contract model) plus a Codecov-style complexity hotspot SVG. Ships [`complexity-treemap.py`](skills/assess/scripts/complexity-treemap.py) so the agent runs the treemap with no external setup. Filters build artifacts and generated code by default (opt-out with `--include-artifacts`); warns when one file dominates LOC. Offers to install optional `scc` for repos heavy in markdown/JSON/YAML. Generated PRs include a self-install footer so reviewers can adopt the plugin. |
| `/huddle` | Multi-perspective deliberation using Six Thinking Hats with Fibonacci team sizing (solo -> debate -> huddle -> panel -> board). Three execution modes: solo flat-parallel, phased sub-agent (default fallback), and team mode (needs Agent Teams capability flag). |

### Commands (slash-only, no bundled assets)

Portable:

| Command | Description |
|---------|-------------|
| `/6hats <question>` | Solo Six Hats analysis - alias for `/huddle` at team size 1 |
| `/understand <thing>` | Deep understanding mode (nemawashi) - exhaustive context-gathering before action |

Workflow (personal setup, opt-in - see [Adapting](#adapting-for-your-workflow)):

| Command | Description |
|---------|-------------|
| `/tm` | Task Master orchestration - context-aware: starts, reviews, or cleans up tasks based on current state |
| `/tm-marathon-config-example` | Reference configuration block to drop into a project's `CLAUDE.md` for marathon-mode `/tm` |
| `/fix-pr` | Autonomous PR fixing loop - iterates on CI failures and review comments until green |
| `/fix-develop` | Autonomous fix loop for failing CI on the repo's default branch |

### Agents (invoked by skills, or directly via `Task(subagent_type=...)`)

The Six Hats team that `/huddle` and `/6hats` orchestrate:

| Agent | Role |
|-------|------|
| `white-hat` | Facts and evidence |
| `red-hat` | Gut feelings and emotional drivers |
| `black-hat` | Risks and critical analysis |
| `yellow-hat` | Benefits and opportunities |
| `green-hat` | Creative alternatives |
| `blue-hat` | Synthesis and recommendation |
| `scribe` | Structures hat output into actionable documentation |

## Why Six Hats?

A blind spot detector for high-stakes decisions, based on Edward de Bono's method.

**Cost:** 5-10x the tokens of a single prompt. Six parallel agents plus synthesis adds up.

**Benefit:** Catches the question you didn't know to ask. Black Hat might reveal your "performance optimization" is really about deployment fear. Green Hat might find the lazy solution that actually works.

**Real example:** an earlier draft of this README was reviewed via `/6hats review please`. Black Hat called it "a 13-point solution to a 2-point problem" with "rigged comparisons" and "zero evidence." That critique led to a rewrite. A single prompt wouldn't have been that harsh.

**Use it for:** architecture decisions you can't easily reverse, "should we..." questions where you suspect you're asking the wrong question, decisions where being wrong costs 100x more than the analysis, when you want pushback rather than validation.

**Skip it for:** routine decisions, debugging, implementation details, anything you can reverse. For those, a single well-crafted prompt is enough:

```text
Help me decide [X]. Be opinionated. If this is a bad idea, say so directly.
What am I not considering? What's the lazy solution that might work?
```

## Adapting for your workflow

The framework pieces (`/assess`, `/huddle`, `/6hats`, `/understand` and their agents) are reusable as-is. The workflow commands embed assumptions you will likely need to override:

- **Directory layout** - `commands/tm.md`, `commands/fix-pr.md`, `commands/fix-develop.md` all assume `~/dev/github.com/<org>/<repo>/<repo>-main/` + sibling `worktree/`. Edit the path patterns to match your structure.
- **Default branch** - `/fix-develop` derives the branch via `gh repo view --json defaultBranchRef`. `/tm` uses a `$BASE_BRANCH` variable. Other commands may still reference `develop` in prose; check before relying on them on a `main`-default repo.
- **Required external tools** - `gh` CLI for GitHub, [Task Master](https://github.com/eyaltoledano/claude-task-master) for `/tm`, optional Agent Teams capability flag (`$CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) for `/tm` marathon mode.
- **Review-bot conventions** - PR-loop logic in `/tm`, `/fix-pr`, `/fix-develop` distinguishes CodeRabbit, claude[bot], and human threads. Adjust if your repo uses different bots.
- **CLAUDE.md** - your global / project `CLAUDE.md` references to the directory structure need to match.

### Git workflow assumed by `/tm`, `/fix-pr`, `/fix-develop`

```text
~/dev/github.com/<org>/<repo>/
├── <repo>-main/                    # SACRED - always on default branch, never modified
└── worktree/
    ├── <tag>/                      # Task Master tag folder (nested)
    │   ├── 1--create-schema/       # Task worktree
    │   └── 2--add-api/             # Another task worktree
    └── fix-login-bug/              # Non-TM worktree (flat)
```

The key principle: **never work directly on the default branch**. `<repo>-main/` stays on `develop`/`main` and clean; all work happens in worktrees. The `/tm` command handles worktree creation and cleanup automatically. To create one by hand:

```bash
cd ~/dev/github.com/<org>/<repo>/<repo>-main
git checkout develop && git pull origin develop
git branch fix-login-bug
git worktree add ../worktree/fix-login-bug fix-login-bug
cd ../worktree/fix-login-bug
# work, commit, push, create PR
```

## Repository structure

```text
ai-native-toolkit/
├── README.md
├── .claude-plugin/
│   ├── plugin.json                    # Plugin manifest (enables /plugin install)
│   └── marketplace.json               # Marketplace entry (enables /plugin marketplace add)
├── skills/
│   ├── assess/
│   │   ├── SKILL.md                   # Codebase readiness assessment + complexity hotspot
│   │   └── scripts/
│   │       └── complexity-treemap.py  # Codecov-style hotspot SVG generator
│   └── huddle/
│       └── SKILL.md                   # Multi-lens Six Hats deliberation
├── commands/
│   ├── tm.md
│   ├── tm-marathon-config-example.md
│   ├── 6hats.md
│   ├── understand.md
│   ├── fix-pr.md
│   └── fix-develop.md
├── agents/
│   ├── white-hat.md
│   ├── red-hat.md
│   ├── black-hat.md
│   ├── yellow-hat.md
│   ├── green-hat.md
│   ├── blue-hat.md
│   └── scribe.md
├── scripts/                           # Standalone skill ZIP build pipeline
│   ├── transform_skill.py             # Marker-based SKILL.md transformer
│   ├── standalone_skill_config.py     # Per-skill config (names, descriptions, replacements)
│   ├── build-standalone-skills.sh     # Build orchestrator
│   ├── pyproject.toml
│   └── tests/
│       ├── test_transform.py          # Transformer unit tests
│       └── test_integration.py        # Full-build ZIP content validation
├── dist/                              # Generated ZIPs (gitignored; published via CI)
└── docs/
    └── example-heatmap.svg            # Sanitized real-world /assess output (README hero)
```

## Contributors

Thanks to [@franklywatson](https://github.com/franklywatson) (Jerome Pimmel) for the standalone skill ZIP pipeline that makes `/assess` and `/huddle` installable in Claude Desktop chat and Cowork ([#24](https://github.com/bjcoombs/ai-native-toolkit/pull/24)).

## License

Licensed under the Apache License, Version 2.0 - see [`LICENSE`](LICENSE) for the full text.

- The Six Thinking Hats method is the intellectual property of Edward de Bono. Licensing covers only this implementation, not the underlying methodology.
- The Task Master commands are designed for use with [Claude Task Master](https://github.com/eyaltoledano/claude-task-master) by Eyal Toledano.
