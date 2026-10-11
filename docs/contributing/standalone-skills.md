# Standalone skill pipeline

`assess`, `huddle`, `deslop`, `skill-forge`, and `semantic-compress` also ship as standalone ZIPs for claude.ai, Claude Desktop, Cowork, and any other Agent-Skills assistant via `Settings → Customize → Skills → Upload Skill`. The set is whatever `SKILLS` in `scripts/standalone_skill_config.py` lists. Back to the [Map of Content](../index.md).

## Each ZIP must be self-contained

A standalone ZIP ships one skill with no siblings, so a plugin-time cross-skill link (`../<other-skill>/...` or `skills/<other-skill>/...`) cannot resolve. Two mechanisms keep ZIPs whole:

- `bundle_files` vendors a needed file from another skill into the ZIP (for example skill-forge vendors `ab-equivalence`'s `runner-prompt.md`, the wrapper its solo mode fills).
- The build's link localizer (`transform_skill.localize_cross_skill_links`) rewrites every cross-skill reference: to the local copy if vendored, otherwise degraded to a bare mention.

`ab-equivalence` is a **library skill** (composed by skill-forge and semantic-compress, never invoked directly), so it ships **no** standalone ZIP of its own; its files are vendored into the consumer that uses them. `scripts/tests/test_integration.py::test_no_dangling_cross_skill_references` fails the build if any shipped `.md` still carries a `../<skill>` or `skills/<skill>` path - the guardrail that was missing when the ab-equivalence extraction left skill-forge's runner reference dangling.

## Build locally

```bash
bash scripts/build-standalone-skills.sh            # all skills → dist/standalone-skills/
bash scripts/build-standalone-skills.sh assess     # one skill
bash scripts/build-standalone-skills.sh --dest ~/Desktop  # custom output dir
```

## How it works

HTML comment markers in source `SKILL.md` files flag plugin-only content. `scripts/transform_skill.py` strips `<!-- chat-skip:start/end -->` blocks and applies `<!-- chat-replace:key -->` substitutions defined in `scripts/standalone_skill_config.py`.

## Tests

- `scripts/tests/test_transform.py` - unit tests for transformer primitives.
- `scripts/tests/test_integration.py` - full-build ZIP content validation (forbidden strings, expected files).
- Run: `cd scripts && uv run --with pytest pytest -v`. `tests.yml` runs both the `skills/assess/` suite and the `scripts/` suite on every PR and push.

## CI

`.github/workflows/build-standalone-skills.yml` triggers on `plugin.json` version bumps and publishes a per-version immutable release tagged `standalone-skills-v<version>`: one create call, no delete. GitHub immutable releases permanently reserve a tag once it has backed a release, so the old rolling `standalone-skills-latest` delete-and-recreate pattern can never succeed again.

Users do not browse these directly. The per-marathon plugin release (`v<version>`, marked Latest) links to that version's `standalone-skills-v<version>` bundle in its notes, so the README points at `releases/latest` and the release page hands off to the ZIP tag (see [Releasing](./release.md)).

## Marker rules

- `<!-- chat-skip:start/end -->` wraps content to remove entirely (plugin path resolution, `$ARGUMENTS`, agent-orchestration infrastructure, namespaced slash commands).
- `<!-- chat-replace:key -->` + next line replaces one line with the standalone text defined in `standalone_skill_config.py`.
- Apply markers to ALL `.md` files in the skill directory, not just `SKILL.md`: reference files with plugin-specific content leak into the ZIP if unmarked.
- Markers must be balanced. Keep them at line start (the transformer handles indented markers via `.strip()`, but line-start is cleaner).
- Run `cd scripts && uv run --with pytest pytest -v` to validate after any marker change.

**When to add markers:** any new skill content that references `${CLAUDE_SKILL_DIR}`, `$ARGUMENTS`, a namespaced slash command (`/ai-native-toolkit:*`), or a Claude Code-only tool (`Agent`, `SendMessage`, a `run_in_background` teammate spawn).
