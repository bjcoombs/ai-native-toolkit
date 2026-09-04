# Plugin Modernization: Intent

This file is the in-repo source of record for the programme's Problem Statement, Decisions, Deprecation Path, Programme sequence, and Success Criteria. The sections were transcribed verbatim from the programme PRD, an un-versioned Task Master working document kept outside the repository. Decisions are cited by ID and not re-argued here.

## Problem Statement

The repo was laid out against the Claude Code plugin model of early 2026: one plugin, a `commands/` directory for slash commands, `skills/` for auto-discovered skills, and no evals. Three things have moved since:

1. **Commands were merged into skills.** The Claude Code docs now state "Custom commands have been merged into skills" and, for plugins, list `commands/` as "Skills as flat Markdown files. Use `skills/` for new plugins." No doc calls `commands/` deprecated; the argument is hygiene, not deprecation. Every property the old command format had (`disable-model-invocation`, `argument-hint`, `$ARGUMENTS`) is a skill frontmatter field. The seven files in `commands/` are already functionally skills; only the flat-file format is legacy.
2. **The single bundle taxes every session.** `claude plugin details ai-native-toolkit` reports **~3,287 always-on tokens added to every session** for 20 skills and 9 agents. Per family, measured from the same output: assess ~490, huddle ~510, deslop ~240, skill-craft ~820, gh-org ~570, delivery ~580, leaked non-components ~90. A user who installed for `/assess` pays for `/tm`, `/ghsync`, the six hat agents and the marathon library. The marketplace format supports several plugins from one repo, which is how the context cost becomes opt-in per family. The saving exists under every umbrella option and is reachable under none without the user uninstalling the umbrella; the options differ only in what happens to the user who does nothing.
3. **Two component directories leak non-components.** The same `details` output lists `README` as a skill (from `commands/README.md`), `README` as an agent (from `agents/README.md`), and `tm-marathon-config-example` as a skill. `agents/README.md` is currently exposed to every session as a selectable subagent named `ai-native-toolkit:README`.

Alongside those, Anthropic's AI-Native SDLC playbook (21 Aug 2026) and the skill authoring best-practices guide set expectations the repo does not yet meet: `SKILL.md` body under 500 lines (bodies measured from the tree: marathon 550, huddle 515, `assess-layer-scorer` 532; `assess` is 494 and already compliant), `CLAUDE.md` "under a page" (currently 223 lines / 23.5 KB), evals as regression tests for the agent configuration (none exist), a `REVIEW.md` review policy at repo root, and subagents that declare tool restrictions (all nine agents declare only `name`/`description`/`model`/`color`).

Two further findings:

4. **The floor cannot see a move.** `scripts/floor_check.py` compares a hard-coded `MARKED_FILES` list at merge-base against the working tree. A moved marked file reads as a deletion (fails) or, if the list is edited in the same PR, as a new file with no base (passes blind). The floor's marker list lives outside `FLOOR.md`'s protected set, and clause iii's "out-of-band sign-off" names no artefact. Every path move in this programme, and every future one, would be a self-signed constitutional change.
5. **The action's only end-to-end test is warn-only by design.** `assess-gate.yml` runs `uses: ./` with `continue-on-error: true` on the render step and degrades a scripts-not-found failure to a `::notice`; `test_action_contract.py` asserts nothing about `working-directory`. A stale path at `action.yml:89/:113` ships green.

## Decisions

Decisions with competing trade-offs are settled here, not inside the specs. Each records the alternatives so a later reader can see why.

| ID | Decision | Status | Position | Why, and what was rejected |
|----|----------|--------|----------|-----------------------------|
| D1 | Split granularity | Decided | Six family plugins under `plugins/<name>/`, each a marketplace entry with `source: "./plugins/<name>"`. No `metadata.pluginRoot`. | Plain `./` sources have no version floor and pluginRoot is ignored for them anyway (plugin-marketplaces.md). Rejected: three coarser plugins (loses the per-family opt-in that is the point); marketplace-root shared `skills/` with per-entry lists (documented for the opposite layout; needs `strict: false` per entry; whole-repo cache copy) |
| D2 | Versioning | Decided | Per-plugin semver. **`assess` bumps MINOR to 1.57.0**; repo `v*` tags track `assess` only. Other five families start at 2.0.0. Marketplace entries carry no `version` field. `{plugin}--v{version}` tags only if a dependency range ever needs one. | `assess_core.py:388-390` defines MAJOR as "breaking change to the deterministic core" and `assess_gate.py:140` skips the regression compare on a major bump: a 2.0.0 tag would disarm every adopter's frozen gate for a pure move. The action contract (D7) is unchanged, so the honest bump is MINOR. Nothing reads the other families' major digit. Omitting entry `version` removes a hot file and the parity warning (plugin.json wins per plugins-reference). Rejected: lockstep (a `/deslop` typo fix lands a Dependabot PR on every action consumer); per-plugin repo tags for the action (breaks the `uses:` pin) |
| D3 | Spec location | Proposed | Specs in repo under `docs/design/`, parsed by path; `.taskmaster/docs/` holds only this programme PRD | Playbook: every stage commits an artifact the next stage can read |
| D4 | Skill naming | Decided | Keep the imperative names. Fold `6hats` into `huddle` as a size argument; `ghsync`/`ghreport` unchanged. | The best-practices guide makes gerund "consider" and action-oriented "acceptable"; its only hard rule is consistency within the collection, met per plugin. Forwarding aliases cannot work for `disable-model-invocation` skills (skills.md: "Claude Code blocks the call"), so six of the eighteen renames had no honest breadcrumb. Renaming `ghsync` breaks `ghreport.sh:283`'s sibling lookup. Rejected: the gerund table (zero platform pull, maximum user churn, doubled `/` menu, deprecation nag on every `/assess`) |
| D5 | Umbrella for existing installs | Proposed, 3 probes | **Symlink meta-plugin**: `plugins/ai-native-toolkit/` with its own `plugin.json` (name `ai-native-toolkit`, 2.0.0) whose `skills/<x>` and `agents/<x>.md` are symlinks into the six families; marketplace entry `source: "./plugins/ai-native-toolkit"`. No `dependencies`, no `strict: false`, no root manifest. | plugins-reference "Share files within a marketplace with symlinks": "This lets a meta-plugin's `skills/` directory link to skills defined by other plugins in the marketplace" - the docs' own description of this case. Keeps the plugin id, every skill name, `/ai-native-toolkit:<x>`, and bare agent names for every existing install; cache copy is the umbrella plus dereferenced targets, not the repo. Costs: bumps with any family bump (cache key); cannot be dogfooded via `--plugin-dir` (external symlinks are skipped for local installs) - only through a GitHub-sourced marketplace; a user who installs a family while keeping the umbrella double-registers (mitigated by the notice: install families first, then uninstall the umbrella). Rejected: dependencies-only umbrella (forces all six on, blocks disabling any, `/plugin update` does not install new deps so update-then-restart lands on zero skills with the plugin disabled, cannot answer `/ai-native-toolkit:assess`); hybrid root entry with `strict: false` (documented in pieces never as a whole, every silent piece silent toward double-loading, whole-repo cache copy on every bump); `renames -> null` (a tombstone: recurring notice for managed-settings users, six manual installs). Fallback if the `agents/` probe fails: hybrid root entry |
| D6 | Cross-agent scope | Decided | Open-standard ZIPs only (R17); no native adapters | Prior decision, no demand |
| D7 | `action.yml` location | Decided | Stays at repo root; CI job `name:` literals stay unchanged | Consumers pin the root path and `v*` tags. Branch protection names `skills/assess pytest` literally; the context is the job name not the path, so moving only `working-directory` needs no PATCH and stalls no open PR. The misleading name is the cost; a later rename is its own PR |
| D8 | Migration shape | Decided | Two PRs: PR0 (WS0, floor only) then PR1 (WS1+WS2, zero floor edits). No staging. | Staging is dead: a visible deletion of a marked file is what the floor blocks (simulated: `git rm skills/marathon/SKILL.md` fails the floor 4x, no bypass), so a three-PR path only moves the blind MARKED_FILES edit, it does not remove it; stacked PRs run none of the required pytest checks (`pull_request: branches: [main]`); the old `commands/` copy wins over a listed skill during any window (tested). Rejected: one atomic PR (self-signed constitutional amendment: MARKED_FILES edited in the diff that deletes the marked files, floor prints "ok") |
| D9 | Huddle transfer set | Decided | Build an 8-case huddle transfer set as a WS4 prerequisite task; its 5 script-graded cases double as WS9's huddle evals | `skills/huddle/` holds only `SKILL.md`; there is no huddle transfer set to gate a split. No transfer set means no `ab-equivalence` gate for the 515-line split |

## Deprecation Path

| Change | Breadcrumb | Removed in |
|--------|-----------|------------|
| Single plugin split (R1) | Symlink meta-plugin `ai-native-toolkit` keeping id, namespace, and every name; once-only notice | 3.0.0 (`renames -> null`) |
| `6hats` folded into `huddle` (R12) | Stub skill under the old name, forwards with the size argument | 3.0.0 |
| `commands/` deleted (R2) | Row in `docs/migration-2.0.md`; git history | never (a file in `commands/` loads as a skill) |
| `agents/README.md`, `commands/README.md` removed (R3) | Content in plugin READMEs; row in the guide | never |
| `docs/superpowers/` renamed (R13) | Redirect stub | 3.0.0 |
| `.github/claude-review-instructions.md` replaced (R8) | Three-line pointer | 3.0.0 |
| `CLAUDE.md` sections moved (R7) | One-line pointer per moved section | never |
| `MARKED_FILES` deleted (R0) | Token discovery; `FLOOR.md` describes the token | never |
| Assess helpers not folded (Out of scope) | Listed on the 3.0 list in the guide | 3.0.0 |

## Programme sequence

0. **Spec PR** - creates `docs/design/2026-09-modernization/` with `intent.md` and eleven `spec.md` files (WS0-WS10); records Probes 1-3. Reviewed and merged before any workstream starts; each spec is parsed into its tag on merge.
1. **PR0 (WS0)** - floor only. Approved through the `floor-signoff` environment it introduces. Includes the R19 guards.
2. **PR1 (WS1 + WS2)** - first commit a bare `git mv` (so the floor reads R100), second commit every consumer from the Technical Context table plus the R11 assertions. Zero floor edits, zero job renames. Verified with `claude --plugin-dir plugins/<name>` per family and `claude plugin validate --strict`; the meta-plugin is verified from a GitHub-sourced marketplace (Probe 1). Tag `v1.57.0` on the last commit after the post-merge self-test is green.
3. **WS3, WS5, WS6, WS7** - in parallel, each as the PR list its spec defines. Marathon candidates once PR1 has merged.
4. **WS4** - the huddle transfer set first, then one PR per oversize body with its `ab-equivalence` verdict and directness gain.
5. **WS8** - one PR per candidate, after WS4.
6. **WS9** - cases exist from WS4; the runner and `evals.yml` land after WS8.
7. **WS10** - one deferred task at the next MAJOR.

## Success Criteria

- `claude plugin details` per family: only intended components; `assess` alone under 600 always-on tokens; the meta-plugin lists every family skill and agent once with no `README`.
- `claude plugin validate .` zero warnings; every plugin validates `--strict` in CI.
- `commands/` absent; `agents/README.md` absent; no `README` component anywhere.
- An existing `ai-native-toolkit@ai-native-toolkit` install resolves `/assess`, `/huddle`, and `/ai-native-toolkit:assess` after `/plugin update` and a restart, and sees the notice once (Probe 1 recorded).
- Every consumer of `uses: bjcoombs/ai-native-toolkit@v1.x` sees a MINOR bump to 1.57.0 and an armed regression gate after it.
- `huddle`, `marathon`, `assess-layer-scorer` bodies under 500 lines; `CLAUDE.md` under 8 KB expanded.
- PR1 merges with zero edits to `floor_check.py`, `floor_anchor.py`, `floor.yml`, or `FLOOR.md`, and no branch-protection change.
- All existing required checks green on `main` after each merge under their unchanged names.
- `/assess` self-run: instruction-file layer Present, no new orphans, no phantom seam.
- Every stub, pointer, and the 3.0 list are in `docs/migration-2.0.md` with `remove-in: 3.0.0`; one `deferred` task enumerates the removals.
- Two guardrail hooks and the notice hook each have a passing fixture test.
- Every plugin has a `CHANGELOG.md` entry for its first version; the bump-requires-changelog and family-bump-requires-umbrella-bump tests are green.
- Every standalone ZIP passes the spec validator.
