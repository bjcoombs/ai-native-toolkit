# CLAUDE.md

In-repo contract for agents editing `ai-native-toolkit`. Repo-specific rules only; global conventions (voice, workflow, worktrees) live in the user's `~/.claude/CLAUDE.md`. Long reference material lives under `docs/contributing/`, one pointer per topic below.

## Scope of this repo

Source for the `ai-native-toolkit` Claude Code plugin. Portable skills (`/assess`, `/huddle`, `/deslop`, `/ghsync`, `/skill-forge`, `/semantic-compress`), user-invoked framework skills (`/6hats`, `/understand`), opt-in personal workflow skills (`/tm`, `/issues`, `/fix-pr`, `/fix-develop`, `/tm-marathon-config-example`), and team-orchestration library skills (`marathon`, `pr-review-merge`, `ab-equivalence`) composed by the other skills rather than invoked standalone. Every component is a skill under `skills/<name>/SKILL.md` or an agent under `agents/`; there is no `commands/` directory.

The deliverable is markdown, plus a Python deterministic core under `skills/assess/scripts/` (with `lib/`) and a standalone-skill build under `scripts/`. No application runtime. The pytest suites, the ruff + mypy gates and the floor checks gate merge.

## North star

Make an AI contributor feel like an engineer eighteen months into the org: the codebase supplies the tenure through *externalized context*. Aim for **legibility you can trust, not omniscience you can't**: answers anchored to code a human can spot-check. Guardrails protect the contributor by construction (poka-yoke) against three known tendencies: **accretion** (files only grow), **unactioned intent** (TODO and "remove after" markers nobody keeps) and **guardrail erosion** (loosening a check to turn red green).

**Feature test.** Does the change help a fresh agent (the map), keep answers verifiable (trust), *and* make the wrong action hard (guardrails)? Name the tendency each new signal or rule compensates for; one that traces to no failure mode is decoration. Prefer honest-degrade over impressive-but-wrong. Full text: `docs/contributing/north-star.md`.

## Commands

Run from the repo root.

```bash
make setup                  # sync the locked dev environments (uv), warn if rg is missing
make check                  # every merge-gating suite, lint and type check: run before pushing

# One gate at a time (Makefile targets mirroring the tests.yml jobs; `make help` lists them):
make test-assess            # skills/assess pytest, with its coverage floor
make test-scripts           # scripts/ pytest, with its coverage floor
make test-contract          # plugin contract pytest (tests/)
make lint                   # ruff and mypy for both packages

bash scripts/build-standalone-skills.sh   # standalone ZIPs into dist/standalone-skills/
```

`skills/assess/tests/conftest.py` nulls out global git config for that suite only. The `scripts/` tests and scripts run outside pytest (for example `assess_core.py`) still read it: prefix them with `GIT_CONFIG_GLOBAL=/dev/null` if your config signs commits or runs hooks.

## Versioning

Semver; the version lives in `.claude-plugin/plugin.json` under `.version`.

| Bump | When |
|------|------|
| MAJOR | Breaking change to a skill or command (rename, removed flag, behaviour change users would need to adapt to) |
| MINOR | New skill, new command, new feature on an existing skill (`--include-artifacts` was a MINOR) |
| PATCH | Bug fix, doc-only change, refactor with no user-visible effect |

**Bump in the same PR as the change.** `/plugin update` is version-based: users see "already at latest" and miss changes if the version does not move. A trailing version-bump PR is a fix-up, not the pattern.

## File conventions

### `skills/<name>/SKILL.md`

- `name` - kebab-case, matches the directory name.
- `description` - **must include a `TRIGGER` clause** naming the user phrases that should activate the skill; Claude Code's router matches on it. Exception: a user-invoked skill must *not* carry one.
- Bundled executables go under `skills/<name>/scripts/`, reference docs under `skills/<name>/references/`.

**User-invoked skills** (`/tm`, `/issues`, `/fix-pr`, `/fix-develop`, `/6hats`, `/understand`, `/tm-marathon-config-example`) are slash-command-only:

- `disable-model-invocation: true` - Claude never loads the skill on its own, so a workflow that commits, pushes or spawns a team cannot fire off a `description` match. Uploaded to Claude Desktop / claude.ai it shows Trigger "Slash command", not "Slash command + auto".
- `description` - shown in `/help` and the slash menu, with no `TRIGGER` clause, because the router never reads it. `tests/test_plugin_contract.py::test_skill_has_trigger_clause` enforces both directions.
- `argument-hint` - shown after the command name. Quote a value that opens with `[`, which YAML reads as a list; `test_skill_frontmatter_is_valid_yaml` fails on frontmatter no YAML parser accepts.

`$ARGUMENTS` works in a skill. Bare `$0`..`$9` are substituted in every `SKILL.md`, fenced or not, so write `${0}`, `${1}` (details in `docs/contributing/ci.md`).

### `agents/<name>.md`

- `name` - kebab-case, matches the filename.
- `description` - one-line behaviour summary.
- `model` - typically `inherit`.
- `color` - one of: `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, `cyan`, the set [the sub-agents docs](https://code.claude.com/docs/en/sub-agents.md) list. Any other value is ignored without an error; `test_agent_frontmatter` in `tests/test_plugin_contract.py` enforces the set.

Every `.md` under `agents/` loads as an agent, so the directory holds agent files only. The agent catalog is the Agents section of `docs/index.md`; `test_every_agents_md_is_an_agent` fails the build on a stray doc.

### `commands/` (legacy; do not add)

Claude Code's docs call flat `commands/<name>.md` files the older form of skills. The seven former commands moved to `skills/<name>/SKILL.md` with their invocation names unchanged (https://github.com/bjcoombs/ai-native-toolkit/issues/500). A new slash-command workflow is a user-invoked skill; `test_no_legacy_command_files` fails the build if a `commands/*.md` file reappears.

## Invariants

- Every plugin listed in `.claude-plugin/marketplace.json` exists on disk.
- Every skill auto-discovered from `skills/` has a valid `SKILL.md`.
- A user-invoked skill that orchestrates agents names the agent explicitly; a pure orchestrator (no subagent invocation) says so in its body.
- Relative markdown links resolve. For an illustrative file mention use inline code (`` `CLAUDE.md` ``), not a clickable link.
- No line-starting git conflict marker in any authored markdown; write a marker as inline code.
- mypy `strict` with `disallow_any_generics`: no bare `dict` or `list` (use a `TypedDict`; shared run-context and orchestrator file shapes live in `lib/run_context_types.py`), and every `# type: ignore` carries a code.
- **File-size ratchet.** Python files under `skills/assess/` and `scripts/` stay under 800 lines, or under their `[ceilings]` entry in `.file-size-ratchet.toml`. Split a file rather than raise its ceiling; a ceiling follows its file down.
- **Coverage floor.** `fail_under` in each package's `pyproject.toml` (93 for `skills/assess`, 91 for `scripts/`) only rises; lowering it needs a stated reason in the PR.
- `/assess` layering is inward-only: a `lib/` module never imports an orchestrator script (`skills/assess/tests/test_self_architecture.py`). Add a test alongside any change to a deterministic module, and update `skills/assess/scripts/lib/README.md` (the per-module reference and co-change seam map) with it.
- `instructions_grade` is `Optional[str]`: `null` means no instruction file at any known location, a different remediation from F.
- **`FLOOR.md` is the constitutional floor.** A change to the floor core (`FLOOR.md`, `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, per `FLOOR_CORE_PATHS`) needs the maintainer's out-of-band `floor-signoff` approval, which CI requests automatically. No automated step - retro, marathon teammate, or fix loop - may self-apply it or route around it: propose the change and stop. A change to `scripts/contract/` or the canaries must pass `canary suite (semantic layer)` instead.
- A standalone ZIP is self-contained: no `../<skill>` or `skills/<skill>` path survives the build. New content that names `${CLAUDE_SKILL_DIR}`, `$ARGUMENTS`, an `/ai-native-toolkit:*` command or a Claude Code-only tool needs `chat-skip` / `chat-replace` markers.

## CI

`main` has branch protection (`enforce_admins: true`, 0 required approvals). Seven checks must be green: `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, `ruff + mypy gates`, `Validate PR title`, `floor enforcement`, `floor self-anchor`. PR titles follow conventional-commit format. CI installs from the committed `uv.lock` files (`uv run --locked`), and the `Lockfiles match pyproject.toml` step fails on a stale one: a dependency edit commits the re-locked `uv.lock` (run `uv lock` in that package). CI runs only on PRs to `main` and pushes to `main`. A red test is a real regression; the core is deterministic.

Workflows, lockfiles, the type gate, the ratchet and coverage details, mutation testing, CodeQL and the coverage recipe for `/assess`: `docs/contributing/ci.md`.

## Marathon Configuration

Read by `/tm`, `/issues`, `marathon`, `pr-review-merge` and `/fix-pr`. Without it they default to 1 approval, which stalls every PR on this solo repo. Reasoning behind each value: `docs/contributing/marathon.md`.

### Branch and Merge

- **Base branch**: `main`
- **PR target branch**: `main`
- **Required approvals**: 0 - solo maintainer; the lead merges at green CI + resolved threads.
- **Markdown-only PR approvals**: 0

### Bot Reviewers

**CodeRabbit** (`coderabbitai[bot]`):
- Re-reviews on push: no - rate-limited, check often `null`; never gates merge.
- Not a required status check. Fix code and push; it resolves its own threads. **Never reply in CodeRabbit threads.**

**claude-review** (`claude[bot]`, advisory AI review workflow):
- Re-reviews on push: yes
- Max wait for re-review: 20m
- Re-review check name: claude-review
- Merge hold follows `pr-review-merge` Ready Criterion 6's four-state rule on the head SHA: `success` (confirm the `Commit:` line cites the head) or `skipped` satisfies it; any other settled conclusion, or expiry, merges with a warning naming `claude-review`, the head SHA and the conclusion. Never holds forever.
- Resolve its threads via GraphQL after addressing the feedback.

No human reviewers on this repo.

### CI Patterns

- **Required (merge-gating) checks**: `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, `ruff + mypy gates`, `Validate PR title`, `floor enforcement`, `floor self-anchor`.
- **Non-blocking checks**: `CodeRabbit`, `Auto-label from PR title`, `canary suite (semantic layer)`, `canary path filter`, `floor sign-off summary`, `floor sign-off` (gates indirectly via `floor enforcement`), `build`, `mutmut (assess core)`, `CodeQL (python)`, `CodeQL (javascript-typescript)`, `CodeQL (actions)`, `CodeQL`, `pip-audit`, `claude-review` (confirm its `Commit:` line matches the head).
- **`AI-readiness regression gate`**: effectively required and re-runs on every base advance; not in the branch-protection list, but `gh pr merge` is refused while it runs. Wait for it per PR, or merge with `--admin` once the seven required checks are green.
- **Hot file on every PR**: `.claude-plugin/plugin.json` `.version`. Assign each teammate its target version at spawn (final) and merge sequentially, highest wins. Never give parallel PRs identical bumps: the last-merging PR bumps one step higher (or the wave bumps once at the end) so the standalone bundle republishes.

### GitHub Issues (for `/issues`)

- **Agent-ready label**: `agent-ready`
- **Needs-triage label**: `needs-triage`
- **In-progress label**: `in-progress`
- **Issue exclude labels**: `question`, `wontfix`, `duplicate`, `invalid` (skipped during triage)

### Retrospective

- **Retro log**: `~/.claude/projects/<project-slug>/memory/marathon-retros.md` - append each marathon's retrospective. Never commit a resolved absolute path.

### Release after a marathon

The final step, once every PR is merged and the retro is done: one GitHub release `v<version>` per marathon, with notes that link the `standalone-skills-v<version>` ZIP bundle, then a manual Marketplace refresh. Commands: `docs/contributing/release.md`.

## Reference

- North star in full: `docs/contributing/north-star.md`
- CI, gates, coverage, mutation, CodeQL: `docs/contributing/ci.md`
- Marathon settings explained: `docs/contributing/marathon.md`
- Releasing: `docs/contributing/release.md`
- Standalone skill pipeline (markers, vendoring, build): `docs/contributing/standalone-skills.md`
- `/assess` architecture and the `.assess/` wiki: `docs/contributing/assess-architecture.md`
- Testing an unmerged branch as a real plugin (`/plugin install` only sees `main`), and how `${CLAUDE_SKILL_DIR}` substitution works: `docs/testing-a-branch-locally.md`
- Every shipped doc: `docs/index.md`

Recognised by Claude Code as `CLAUDE.md`. For Codex (`AGENTS.md`) or Gemini CLI (`GEMINI.md`), copy or symlink under those names.
