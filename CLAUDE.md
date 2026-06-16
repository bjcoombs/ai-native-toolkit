# CLAUDE.md

In-repo contract for agents editing `ai-native-toolkit`. Repo-specific rules only - global conventions (voice, workflow, worktrees) live in the user's `~/.claude/CLAUDE.md` and aren't repeated here.

## Scope of this repo

Source for the `ai-native-toolkit` Claude Code plugin. Portable skills (`/assess`, `/huddle`, `/deslop`, `/ghsync`, `/skill-forge`, `/semantic-compress`), portable framework commands (`/6hats`, `/understand`), personal workflow commands that are opt-in (`/tm`, `/issues`, `/fix-pr`, `/fix-develop`), and team-orchestration library skills (`marathon`, `pr-review-merge`, `ab-equivalence`) that are composed by the workflow commands and portable skills rather than invoked standalone.

The deliverable is markdown: agents, commands, skills. It also ships a Python deterministic core under `skills/assess/scripts/` (plus `lib/`) and a standalone-skill build pipeline under `scripts/`. There is no application runtime, but there are pytest suites (`skills/assess/`, `scripts/`), a ruff + mypy lint gate, and a standalone-ZIP build step - all enforced in CI.

## North star

Everything here serves one goal: make an AI contributor feel like an engineer who has been in the org eighteen months, not a brand-new hire. The difference is *externalized context*, not capability. An AI is structurally always the new hire - each session starts with an empty head and one narrow context window - so the codebase has to supply the tenure: a navigable map, load-bearing contracts made explicit, complexity made *locally* legible so the relevant slice fits one keyhole.

The safety half is non-negotiable: the goal is **legibility you can trust, not omniscience you can't.** An agent fluent about code nobody can verify is the dangerous failure, not the win. Answers must stay anchored to code a human can spot-check.

The other half is the same ethic pointed at the write side: when a contributor makes a mistake, ask "what made it possible, and what would make it impossible next time?" - not "who's to blame". Guardrails (linters, architecture tests, CI gates, coverage, review automation - Layers 3-7) aren't a leash on the AI; they protect the contributor from costly mistakes by design, the way an org protects a human engineer with RBAC and staged environments rather than hope. This is **correctness by construction** / poka-yoke: make the wrong action hard and the right action the path of least resistance.

The write-side mistakes worth guarding against are not hypothetical - they are the known tendencies of an AI contributor, observed across models and worth naming so every guardrail traces to one:

- **Accretion.** An agent does what is asked, and what is asked is feature after feature. Nothing in that loop ever asks for a refactor, so files only grow. Absent a consciously requested restructuring, size and complexity ratchet monotonically upward.
- **Unactioned intent.** An agent records promises it never returns to keep: TODO / FIXME / "deprecated, use X" / "remove after migration" comments. These are *promissory markers* - self-descriptions of the code's future with no pressure to come true. Aged and ignored, they are a lying map of intent, exactly as a stale doc is a lying map of behaviour.
- **Guardrail erosion.** Under pressure to make red go green, an agent loosens the check instead of fixing the root - a suppression here, a skipped test there, a widened threshold - hollowing out the very layers meant to protect it while the scaffolding still reads as Present.

All three are the same defect: a self-description (the file's shape, the comment's promise, the gate's verdict) under no pressure to stay true. The toolkit's job is to convert each tendency into a deterministic signal (the marker aged by the edits it survived, the file that only ever grows, the suppression count that climbs) and a ratchet that makes the honest action the cheap one.

**Use this as the feature test.** Judge any change to a skill or report by: does it help a fresh agent (the map), keep its answers verifiable (trust), *and* make the wrong action hard to take (guardrails - protecting the contributor, not policing it)? When adding a signal or rule, name the contributor tendency it compensates for - a guardrail that doesn't trace to a failure mode is decoration. Prefer honest-degrade over impressive-but-wrong, local comprehension over global, and "ground the claim in the file" over a fluent narrative.

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
- **Hot file on every PR**: `.claude-plugin/plugin.json` `.version` - each PR must bump it. Assign each teammate its target version explicitly at spawn and merge sequentially (highest version wins); values assigned at spawn are final - if merge order shifts (e.g. an externally-merged PR advances `main`), fix it at merge time by holding or lead-resolving the conflict, never by re-messaging an in-flight teammate. **Identical bumps across parallel PRs are unsafe here**: `build-standalone-skills.yml` publishes an immutable `standalone-skills-v<version>` on the version *change*, so the first merge ships an incomplete bundle and the identical later bumps never re-fire it - have the last-merging PR bump one step higher (or bump once at the end) so the complete tree republishes.

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

# Prepend a pointer to the standalone ZIP bundle so the README's stable
# releases/latest link can hand off to that version's downloadable ZIPs.
# (build-standalone-skills.yml publishes standalone-skills-v$VERSION on the
# version-bump push, so it exists by the time you cut this release.)
ZIP_URL="https://github.com/bjcoombs/ai-native-toolkit/releases/tag/standalone-skills-v${VERSION}"
BODY=$(gh release view "v$VERSION" --json body -q .body)
gh release edit "v$VERSION" --notes "📦 **Standalone skill ZIPs** (claude.ai, Claude Desktop, Cowork, any Agent-Skills assistant): ${ZIP_URL}

${BODY}"
```

`.github/release.yml` maps the PR labels (`feat`/`fix`/`docs`/`chore`/`refactor`) to release-note categories, and `build-standalone-skills.yml` republishes the standalone ZIPs on the version bump. The plugin release is marked Latest, so `releases/latest` always resolves to it and its notes link straight to the ZIP bundle. One release per marathon, not one per PR.

When several PRs share one marathon, the standalone build fires on the **first** version-changing merge - so the final merged tree must carry the highest version (have the last-merging PR bump one step higher, per the version hot-file note above), otherwise the published `standalone-skills-v<version>` bundle is missing the later PRs and no further build re-fires for that version.

**Marketplace listing (manual, web-only).** The repo also publishes the [AI-Readiness Assess Gate action](https://github.com/marketplace/actions/ai-readiness-assess-gate). The listing's "latest version" does **not** advance with `gh release create` - no API exists for the Marketplace flag. To refresh it: edit the release in the web UI, tick "Publish this Action to the GitHub Marketplace", update. Do this on marathon releases; consumers are unaffected either way (their `uses:` pins and Dependabot bumps read git tags, not the listing).

## Testing a branch before merging

`/plugin install` only sees `main`. To test an unmerged branch's `SKILL.md` + scripts as a real plugin - or to run the scripts directly against a target repo - see [`docs/testing-a-branch-locally.md`](docs/testing-a-branch-locally.md). Key point: plugin skills resolve their bundled scripts via `$CLAUDE_PLUGIN_ROOT` (the version cache dir), not `~/.claude/skills/`.

## Standalone skill pipeline

`assess`, `huddle`, `deslop`, `skill-forge`, and `semantic-compress` are also distributed as standalone ZIPs for claude.ai, Claude Desktop, Cowork, and any other Agent-Skills assistant via `Settings → Customize → Skills → Upload Skill`. The set is whatever `SKILLS` in `scripts/standalone_skill_config.py` lists.

**Each ZIP must be self-contained.** A standalone ZIP ships one skill with no siblings, so a plugin-time cross-skill link (`../<other-skill>/...` or `skills/<other-skill>/...`) cannot resolve. Two mechanisms keep ZIPs whole: `bundle_files` vendors a needed file from another skill into the ZIP (e.g. skill-forge vendors `ab-equivalence`'s `runner-prompt.md`, the wrapper its solo mode fills), and the build's link localizer (`transform_skill.localize_cross_skill_links`) rewrites every cross-skill reference - to the local copy if vendored, otherwise degraded to a bare mention. `ab-equivalence` is a **library skill** (composed by skill-forge and semantic-compress, never invoked directly), so it ships **no** standalone ZIP of its own; its files are vendored into the consumer that uses them. `scripts/tests/test_integration.py::test_no_dangling_cross_skill_references` fails the build if any shipped `.md` still carries a `../<skill>`/`skills/<skill>` path - the guardrail that was missing when the ab-equivalence extraction left skill-forge's runner reference dangling.

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

**CI:** `.github/workflows/build-standalone-skills.yml` triggers on `plugin.json` version bumps and publishes a per-version immutable release tagged `standalone-skills-v<version>` (one create call, no delete - GitHub immutable releases permanently reserve a tag once it has backed a release, so the old rolling `standalone-skills-latest` delete-and-recreate pattern can never succeed again). Users don't browse these directly: the per-marathon plugin release (`v<version>`, marked Latest) carries a link to that version's `standalone-skills-v<version>` bundle in its notes, so the README points at `releases/latest` and the release page hands off to the ZIP tag (see "Release after a marathon"). `.github/workflows/tests.yml` now runs both the `skills/assess/` suite and the `scripts/` suite on every PR and push.

**Marker rules:**
- `<!-- chat-skip:start/end -->` - wraps content to remove entirely (plugin path resolution, `$ARGUMENTS`, agent-orchestration infrastructure, namespaced slash commands)
- `<!-- chat-replace:key -->` + next line - replaces one line with the standalone text defined in `standalone_skill_config.py`
- Apply markers to ALL `.md` files in the skill directory, not just `SKILL.md` - reference files with plugin-specific content will leak into the ZIP if unmarked
- Markers must be balanced; keep them at line start (the transformer handles indented markers via `.strip()`, but line-start is cleaner)
- Run `cd scripts && uv run --with pytest pytest -v` to validate after any marker changes

**When to add markers:** any new skill content that references `SKILL_DIR`, `$ARGUMENTS`, a namespaced slash command (`/ai-native-toolkit:*`), or a Claude Code-only tool (`Agent`, `TeamCreate`, `SendMessage`, `TeamDelete`).

## /assess architecture

Deterministic core in `skills/assess/scripts/lib/` does all data work; the LLM only writes prose. The layering is **inward-only**: a `lib/` module may import other `lib/` modules and third-party libraries, but never an orchestrator script (`assess_core.py`, `assess_finalize.py`, `assess_report.py`, `assess_gate.py`, ...). This keeps the core independently testable and reusable. It is no longer just a convention - `skills/assess/tests/test_self_architecture.py` enforces it as a contract: an `ast` scan fails the build if any `lib/` module imports an orchestrator (the forbidden set is derived from disk, so a new orchestrator is covered automatically).

- `lib/agent_instructions_grader.py` - heuristic scoring of CLAUDE.md / AGENTS.md / GEMINI.md / .cursorrules / .github/copilot-instructions.md (regex + arithmetic, no AI)
- `lib/stats_diff.py` - cross-run comparison (graduated/regressed/new/persistent hotspots)
- `lib/wiki_writer.py` - renders `index.md`, `log.md`, `hotspots/*.md` from templates
- `lib/anomaly_detector.py` - flags suspicious run results for self-feedback
- `scripts/assess_core.py` - orchestrator; writes `run-context.json` for the LLM to read
- `scripts/assess_finalize.py` - LLM write-back; reads `.assess/finalize-input.json` and replaces deterministic-core placeholders in `log.md` and `hotspots/*.md` with the LLM-derived score and per-file actions.

Tests live in `skills/assess/tests/` and run via `uv run --with pytest pytest`. Add a test alongside any change to a deterministic module - that's the contract that lets us trust the output regardless of which LLM is driving.

`skills/assess/scripts/lib/README.md` is the per-module reference and the home of the **co-change seam map**: it names the directories that move together by design (the core <-> lib seam, each lib module <-> its test, and the standalone build <-> the skills it packages) so the historical coupling `/assess` flags on this repo reads as owned cohesion, not entanglement. Update it alongside any change to a `lib/` module so it never becomes a lying map of its own.

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
