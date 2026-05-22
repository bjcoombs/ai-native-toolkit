# CLAUDE.md

In-repo contract for agents editing `ai-native-toolkit`. Repo-specific rules only - global conventions (voice, workflow, worktrees) live in the user's `~/.claude/CLAUDE.md` and aren't repeated here.

## Scope of this repo

Source for the `ai-native-toolkit` Claude Code plugin. Two portable skills (`/assess`, `/huddle`), portable framework commands (`/6hats`, `/understand`), and personal workflow commands that are opt-in (`/tm`, `/fix-pr`, `/fix-develop`).

The deliverable is markdown: agents, commands, skills. The only executable code is `skills/assess/scripts/complexity-treemap.py`. There is no application runtime, no test suite, no build step.

## Versioning

The plugin follows [semver](https://semver.org). Version lives in `.claude-plugin/plugin.json` under `.version`.

| Bump | When |
|------|------|
| MAJOR | Breaking change to a skill or command (rename, removed flag, behaviour change users would need to adapt to) |
| MINOR | New skill, new command, new feature on an existing skill (`--include-artifacts` was a MINOR) |
| PATCH | Bug fix, doc-only change, refactor with no user-visible effect |

**Bump in the same PR as the change.** Claude Code's `/plugin update` is version-based - users see "already at latest" and miss changes if the version doesn't move. A trailing version-bump PR is a fix-up, not the pattern to follow.

## File conventions

### `skills/<name>/SKILL.md`

Required frontmatter:

- `name` - kebab-case, must match the directory name
- `description` - **must include a `TRIGGER` clause** describing the user phrases / questions that should activate the skill. Claude Code's skill router matches on this.

Bundled executables go under `skills/<name>/scripts/`. Reference docs go under `skills/<name>/references/`.

### `agents/<name>.md`

Required frontmatter:

- `name` - kebab-case, must match the filename
- `description` - one-line behaviour summary
- `model` - typically `inherit`
- `color` - one of: `cyan`, `blue`, `red`, `yellow`, `green`, `purple`, `pink`, `orange`, `indigo`

### `commands/<name>.md`

Frontmatter recommended (some existing commands don't have it yet):

- `description` - shown in `/help`
- `argument-hint` - shown after the command name when typing

Commands without frontmatter still work but provide no `/help` description.

## Invariants

- Every plugin listed in `.claude-plugin/marketplace.json` must exist on disk.
- Every skill auto-discovered from `skills/` must have a valid `SKILL.md`.
- Commands that orchestrate agents should explicitly reference the agent name. Commands that are pure orchestrators (no subagent invocation) should say so in their body so an agent reading the file isn't confused.

## CI

- `.github/workflows/pr-lint.yml` validates PR titles match conventional-commit format and auto-applies the matching label (`feat` / `fix` / `docs` / `chore` / `refactor`). Other conventional types (`ci`, `build`, `test`, `perf`, `style`, `revert`) pass validation but aren't auto-labelled.
- `.github/release.yml` configures categorised release notes when running `gh release create --generate-notes`. See the file for the label-to-category mapping.

## /assess architecture

Deterministic core in `skills/assess/scripts/lib/` does all data work; the LLM only writes prose.

- `lib/agent_instructions_grader.py` - heuristic scoring of CLAUDE.md / AGENTS.md / GEMINI.md / .cursorrules / .github/copilot-instructions.md (regex + arithmetic, no AI)
- `lib/stats_diff.py` - cross-run comparison (graduated/regressed/new/persistent hotspots)
- `lib/wiki_writer.py` - renders `index.md`, `log.md`, `hotspots/*.md` from templates
- `lib/anomaly_detector.py` - flags suspicious run results for self-feedback
- `scripts/assess_core.py` - orchestrator; writes `run-context.json` for the LLM to read

Tests live in `skills/assess/tests/` and run via `uv run --with pytest pytest`. Add a test alongside any change to a deterministic module - that's the contract that lets us trust the output regardless of which LLM is driving.

The `.assess/` directory in a target repo is a compounding wiki:

- `assess-report.md` - latest prose-heavy summary (LLM-written)
- `complexity-stats.json` + `.prior.json` - current and previous run sidecars
- `complexity-heatmap.svg` - current treemap
- `run-context.json` - structured data the LLM uses for prose
- `index.md` - catalog of every hotspot ever flagged (deterministic)
- `log.md` - append-only run history (deterministic)
- `hotspots/<slug>.md` - per-file persistent page (deterministic)

Each `/assess` run reads the prior state from this directory and adds to it. Hotspots that leave the top list graduate. The wiki is the value, not any single snapshot.

## What this repo doesn't have (and that's fine)

- Test coverage focused on the deterministic core under `skills/assess/`. `complexity-treemap.py` is still smoke-tested rather than unit-tested - it's a thin CLI wrapper around lizard/squarify.
- No coverage gates - same reason.

## Compatibility

Recognised by Claude Code as `CLAUDE.md`. For Codex (`AGENTS.md`) or Gemini CLI (`GEMINI.md`), copy or symlink under those names.
