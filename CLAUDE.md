# CLAUDE.md

In-repo contract for agents editing `ai-native-toolkit`. Repo-specific rules only - global conventions (voice, workflow, worktrees) live in the user's `~/.claude/CLAUDE.md` and aren't repeated here.

## Scope of this repo

Source for the `ai-native-toolkit` Claude Code plugin. Two portable skills (`/assess`, `/huddle`), portable framework commands (`/6hats`, `/understand`), and personal workflow commands that are opt-in (`/tm`, `/fix-pr`, `/fix-develop`).

The deliverable is markdown: agents, commands, skills. It also ships a Python deterministic core under `skills/assess/scripts/` (plus `lib/`) and a standalone-skill build pipeline under `scripts/`. There is no application runtime, but there are pytest suites (`skills/assess/`, `scripts/`), a ruff + mypy lint gate, and a standalone-ZIP build step - all enforced in CI.

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
- `.github/workflows/tests.yml` runs three pytest jobs (`skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`) plus a `ruff + mypy gates` lint job (Layer 3 complexity ratchet + Layer 2 type gate) on every PR and push to `main`. A red test is a real regression - the deterministic core is reproducible, so flakes shouldn't happen. The `plugin contract pytest` job runs `test_internal_links_resolve` over every shipped `SKILL.md`/command file, so relative markdown links must point to real files - use inline code (`` `CLAUDE.md` ``), not a clickable `[..](./CLAUDE.md)` link, for illustrative file mentions.
- **`main` has branch protection** (`enforce_admins: true`, 0 required approvals): a PR cannot merge unless `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, and `Validate PR title` are green. `CodeRabbit`, `Auto-label from PR title`, and the push-only `build` (standalone publish) job are intentionally **not** required. Emergency override: `gh api -X DELETE repos/bjcoombs/ai-native-toolkit/branches/main/protection`, then re-apply.
- `.github/release.yml` configures categorised release notes when running `gh release create --generate-notes`. See the file for the label-to-category mapping.

**CI triggers only on `main`** (`pull_request`/`push` to `main`). A PR targeting `main` gets `tests.yml` + `pr-lint.yml` on every synchronize; pushes to other branches with no open PR don't run them.

## Marathon Configuration

Project-specific settings the `/tm` and `/issues` commands (and the shared `marathon` skill) read to drive marathon mode. When this section is absent the commands fall back to defaults (base `main`, **1** approval) - which is wrong for this solo repo and would stall every PR, so keep it here.

### Branch and Merge

- **Base branch**: `main`
- **PR target branch**: `main`
- **Required approvals**: 0 - solo-maintainer repo, no second reviewer or approving bot. The lead merges each PR itself at green CI + resolved threads.
- **Markdown-only PR approvals**: 0

### Bot Reviewers

**CodeRabbit** (`coderabbitai[bot]`):
- Comments only, frequently rate-limited; its check often reports neutral/`null`. It is **not** a required status check and never blocks merge.
- Fix code and push - CodeRabbit re-reviews and resolves its own threads. **Never reply in CodeRabbit threads** (it ignores replies from other bots).

No human reviewers and no `claude[bot]` on this repo.

### CI Patterns

- **Required (merge-gating) checks**: `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, `Validate PR title` (enforced by branch protection - see the CI section).
- **Non-blocking checks**: `CodeRabbit` (rate-limited bot), `Auto-label from PR title` (convenience automation), `build` (push-only standalone publish - does not run on PRs), `claude-review` (advisory AI review - posts findings as threads but never gates merge; slow, often finishes after the required checks are green).
- **`AI-readiness regression gate`**: an `/assess`-based gate that is **effectively required but re-runs on every base advance** (it diffs the PR against `main`). It is not in the classic branch-protection `required_status_checks.contexts`, so it won't show in that API list, but a plain `gh pr merge` is refused while it is re-running. In a multi-PR wave each merge re-triggers it on the still-open PRs - so either wait for it to re-settle per PR, or merge with `--admin` once the four named required checks are green.
- **Local-run gotcha**: the `skills/assess` pytest suite shows ~7 phantom git-commit failures from global git config when run locally - run with `GIT_CONFIG_GLOBAL=/dev/null` to clear them. They are not CI failures.
- **Hot file on every PR**: `.claude-plugin/plugin.json` `.version` - each PR must bump it. Identical bumps 3-way-merge cleanly; divergent bumps conflict, so merge sequentially (highest version wins). Tell each teammate the next version explicitly when running several PRs at once.

### GitHub Issues (for `/issues`)

- **Agent-ready label**: `agent-ready`
- **Needs-triage label**: `needs-triage`
- **In-progress label**: `in-progress`
- **Issue exclude labels**: `question`, `wontfix`, `duplicate`, `invalid` (skipped during triage)

### Retrospective

- **Retro log**: `~/.claude/projects/<project-slug>/memory/marathon-retros.md` - append each marathon's retrospective here after completion. (Path is per-machine; don't commit a resolved absolute path - it leaks a home directory, the exact issue `/assess` now warns about.)

### Release after a marathon

As the **final** step, once all of a marathon's PRs are merged and the retrospective is done, cut a GitHub release at the new plugin version so users get the update via `/plugin update` and the standalone-skills publish + categorised release notes fire:

```bash
VERSION=$(jq -r '.version' .claude-plugin/plugin.json)
gh release create "v$VERSION" --generate-notes --title "v$VERSION"
```

`.github/release.yml` maps the PR labels (`feat`/`fix`/`docs`/`chore`/`refactor`) to release-note categories, and `build-standalone-skills.yml` republishes the standalone ZIPs on the version bump. One release per marathon, not one per PR.

## Testing a branch before merging

`/plugin install` only sees `main`. To test an unmerged branch's `SKILL.md` + scripts as a real plugin - or to run the scripts directly against a target repo - see [`docs/testing-a-branch-locally.md`](docs/testing-a-branch-locally.md). Key point: plugin skills resolve their bundled scripts via `$CLAUDE_PLUGIN_ROOT` (the version cache dir), not `~/.claude/skills/`.

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
- `scripts/tests/test_transform.py` - unit tests for transformer primitives
- `scripts/tests/test_integration.py` - full-build ZIP content validation (forbidden strings, expected files)
- Run: `cd scripts && uv run --with pytest pytest -v`

**CI:** `.github/workflows/build-standalone-skills.yml` triggers on `plugin.json` version bumps and publishes a per-version immutable release tagged `standalone-skills-v<version>` (one create call, no delete - GitHub immutable releases permanently reserve a tag once it has backed a release, so the old rolling `standalone-skills-latest` delete-and-recreate pattern can never succeed again). The newest is always at `releases?q=standalone-skills`. `.github/workflows/tests.yml` now runs both the `skills/assess/` suite and the `scripts/` suite on every PR and push.

**Marker rules:**
- `<!-- chat-skip:start/end -->` - wraps content to remove entirely (plugin path resolution, `$ARGUMENTS`, agent-orchestration infrastructure, namespaced slash commands)
- `<!-- chat-replace:key -->` + next line - replaces one line with the standalone text defined in `standalone_skill_config.py`
- Apply markers to ALL `.md` files in the skill directory, not just `SKILL.md` - reference files with plugin-specific content will leak into the ZIP if unmarked
- Markers must be balanced; keep them at line start (the transformer handles indented markers via `.strip()`, but line-start is cleaner)
- Run `cd scripts && uv run --with pytest pytest -v` to validate after any marker changes

**When to add markers:** any new skill content that references `SKILL_DIR`, `$ARGUMENTS`, a namespaced slash command (`/ai-native-toolkit:*`), or a Claude Code-only tool (`Agent`, `TeamCreate`, `SendMessage`, `TeamDelete`).

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
