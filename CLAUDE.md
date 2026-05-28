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
- `.github/workflows/tests.yml` runs `uv run --with pytest pytest -v` against `skills/assess/` on every PR and push to `main`. A red test is a real regression - the deterministic core is reproducible, so flakes shouldn't happen.
- `.github/release.yml` configures categorised release notes when running `gh release create --generate-notes`. See the file for the label-to-category mapping.

**CI triggers only on `main`** (`pull_request`/`push` to `main`). A PR targeting `main` gets `tests.yml` + `pr-lint.yml` on every synchronize; pushes to other branches with no open PR don't run them.

## Testing a branch before merging

`/plugin install` only sees `main`. To test an unmerged branch's `SKILL.md` + scripts as a real plugin — or to run the scripts directly against a target repo — see [`docs/testing-a-branch-locally.md`](docs/testing-a-branch-locally.md). Key point: plugin skills resolve their bundled scripts via `$CLAUDE_PLUGIN_ROOT` (the version cache dir), not `~/.claude/skills/`.

## Standalone skill pipeline

`assess` and `huddle` are also distributed as standalone ZIPs for Claude Desktop chat and Cowork via `Settings → Customize → Skills → Upload Skill`.

**Build locally:**
```bash
bash scripts/build-standalone-skills.sh            # all skills → dist/standalone-skills/
bash scripts/build-standalone-skills.sh assess     # one skill
bash scripts/build-standalone-skills.sh --dest ~/Desktop  # custom output dir
```

**How it works:** HTML comment markers in source SKILL.md files flag plugin-only content. `scripts/transform_skill.py` strips `<!-- chat-skip:start/end -->` blocks and applies `<!-- chat-replace:key -->` substitutions defined in `scripts/standalone_skill_config.py`. The pipeline is tested via `uv run --with pytest pytest` from `scripts/`.

**Tests:**
- `scripts/tests/test_transform.py` — unit tests for transformer primitives
- `scripts/tests/test_integration.py` — full-build ZIP content validation (forbidden strings, expected files)
- Run: `cd scripts && uv run --with pytest pytest -v`

**CI:** `.github/workflows/build-standalone-skills.yml` triggers on `plugin.json` version bumps and publishes to the `standalone-skills-latest` rolling release. `.github/workflows/tests.yml` now runs both the `skills/assess/` suite and the `scripts/` suite on every PR and push.

**Marker rules:**
- `<!-- chat-skip:start/end -->` — wraps content to remove entirely (plugin path resolution, `$ARGUMENTS`, agent-orchestration infrastructure, namespaced slash commands)
- `<!-- chat-replace:key -->` + next line — replaces one line with the standalone text defined in `standalone_skill_config.py`
- Apply markers to ALL `.md` files in the skill directory, not just `SKILL.md` — reference files with plugin-specific content will leak into the ZIP if unmarked
- Markers must be balanced; keep them at line start (the transformer handles indented markers via `.strip()`, but line-start is cleaner)
- Run `cd scripts && uv run --with pytest pytest -v` to validate after any marker changes

**When to add markers:** any new skill content that references `SKILL_DIR`, `$ARGUMENTS`, a namespaced slash command (`/ai-native-toolkit:*`), or a Claude Code–only tool (`Agent`, `TeamCreate`, `SendMessage`, `TeamDelete`).

## /assess architecture

Deterministic core in `skills/assess/scripts/lib/` does all data work; the LLM only writes prose.

- `lib/agent_instructions_grader.py` - heuristic scoring of CLAUDE.md / AGENTS.md / GEMINI.md / .cursorrules / .github/copilot-instructions.md (regex + arithmetic, no AI)
- `lib/stats_diff.py` - cross-run comparison (graduated/regressed/new/persistent hotspots)
- `lib/wiki_writer.py` - renders `index.md`, `log.md`, `hotspots/*.md` from templates
- `lib/anomaly_detector.py` - flags suspicious run results for self-feedback
- `scripts/assess_core.py` - orchestrator; writes `run-context.json` for the LLM to read
- `scripts/assess_finalize.py` - LLM write-back; reads `.assess/finalize-input.json` and replaces deterministic-core placeholders in `log.md` and `hotspots/*.md` with the LLM-derived score and per-file actions.

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

**The LLM write-back pattern.** The deterministic core writes log/hotspot files with placeholders. After the LLM derives the score, top action, and per-hotspot suggestions, it writes `.assess/finalize-input.json` and invokes `assess_finalize.py` to replace the placeholders in place. This keeps the deterministic core ignorant of LLM-derived content while still producing a wiki where every value is filled.

**Schema convention.** `instructions_grade` is `Optional[str]` - `null` means no instruction file was found at any known location (different remediation from F). The Layer 0 scoring rule in `SKILL.md` distinguishes these cases.

## What this repo doesn't have (and that's fine)

- Test coverage focused on the deterministic core under `skills/assess/`. `complexity-treemap.py` is still smoke-tested rather than unit-tested - it's a thin CLI wrapper around lizard/squarify.
- No coverage gates - same reason.

## Compatibility

Recognised by Claude Code as `CLAUDE.md`. For Codex (`AGENTS.md`) or Gemini CLI (`GEMINI.md`), copy or symlink under those names.
