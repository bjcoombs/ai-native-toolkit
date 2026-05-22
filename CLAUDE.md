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

## What this repo doesn't have (and that's fine)

- No CI - the plugin has no executable surface beyond one Python script. If a frontmatter validator or markdown linter is added, it should live in `.github/workflows/`.
- No tests for the Python script - small enough to smoke-test by running `uv run skills/assess/scripts/complexity-treemap.py .`.
- No coverage gates - same reason.

## Compatibility

Recognised by Claude Code as `CLAUDE.md`. For Codex (`AGENTS.md`) or Gemini CLI (`GEMINI.md`), copy or symlink under those names.
