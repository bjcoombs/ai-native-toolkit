# CLAUDE.md

In-repo contract for agents editing `ai-native-toolkit`. The README is for humans; this file scopes how agents (Claude Code, Codex, anyone else) modify the plugin's source.

Hierarchy: user instructions (`~/.claude/CLAUDE.md`, direct requests) override this file. This file overrides default behaviour.

## Scope of this repo

This is the source for the `ai-native-toolkit` Claude Code plugin. Two portable skills (`/assess`, `/huddle`), portable framework commands (`/6hats`, `/understand`), and personal workflow commands that are opt-in (`/tm`, `/fix-pr`, `/fix-develop`).

The deliverable is markdown: agents, commands, skills. The only executable code is `skills/assess/scripts/complexity-treemap.py`. There is no application runtime, no test suite, no build step.

## Versioning

The plugin follows [semver](https://semver.org). Version lives in `.claude-plugin/plugin.json` under `.version`.

| Bump | When |
|------|------|
| MAJOR | Breaking change to a skill or command (rename, removed flag, behaviour change users would need to adapt to) |
| MINOR | New skill, new command, new feature on an existing skill (`--include-artifacts` was a MINOR) |
| PATCH | Bug fix, doc-only change, refactor with no user-visible effect |

**Bump in the same PR as the change.** Claude Code's `/plugin update` is version-based - users see "already at latest" and miss changes if the version doesn't move. A trailing version-bump PR (like #13) is a fix-up, not the pattern to follow.

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
- Every skill listed in `.claude-plugin/plugin.json` (none currently - the plugin manifest references the bundle, not individual skills) and every skill auto-discovered from `skills/` must have a valid `SKILL.md`.
- Commands that orchestrate agents should explicitly reference the agent name. Commands that are pure orchestrators (no subagent invocation) should say so in their body so an agent reading the file isn't confused.

## Voice

- Imperative, terse.
- **No em-dashes (—)**. Use hyphens (-). Em-dashes are an AI tell.
- No second-person fluff ("you might want to consider..."). Say the thing.
- No emojis unless the user explicitly asks.
- Backtick concrete things: filenames, commands, flag names, code identifiers.
- Tables when comparing options; mermaid when showing flow; markdown lists for sequences.

## Workflow

Per the user's standard layout (see README's "Adapting for your workflow"):

- Default branch: `main`. The PR target.
- **Never edit `claude-config-main/` directly.** That directory is the sacred pristine copy; a hook will block writes.
- All work happens in `../worktree/<branch-name>/`. Create with `git worktree add ../worktree/<branch-name> <branch-name>`.
- Branch naming: `<type>/<short-description>`. Types: `feat`, `fix`, `docs`, `chore`, `refactor`. Examples: `feat/filter-build-artifacts`, `docs/refresh-readme`, `chore/bump-1.2.0`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(scope): summary`, `fix(scope): summary`, `docs: summary`, etc.
- PR titles mirror the commit format.
- PRs are squash-merged.

## What this repo doesn't have (and that's fine)

- No CI - the plugin has no executable surface beyond one Python script. If a frontmatter validator or markdown linter is added, it should live in `.github/workflows/`.
- No tests for the Python script - it's small enough to smoke-test by running `/assess` against this repo (`uv run skills/assess/scripts/complexity-treemap.py .`).
- No coverage gates - same reason.

Adding these in future is good. Not having them isn't a bug.

## Compatibility

This file is recognised by Claude Code as `CLAUDE.md`. If you want it picked up by Codex (`AGENTS.md`) or Gemini CLI (`GEMINI.md`), copy or symlink it under those names. The content is tool-agnostic.
