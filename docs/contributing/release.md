# Releasing

How a marathon ends in a release. Back to the [Map of Content](../index.md).

## Release after a marathon

As the **final** step, once all of a marathon's PRs are merged and the retrospective is done, cut a GitHub release at the new plugin version, so users get the update via `/plugin update` and the standalone-skills publish and categorised release notes fire:

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

When several PRs share one marathon, the standalone build fires on the **first** version-changing merge. The final merged tree must therefore carry the highest version (have the last-merging PR bump one step higher; see [the version hot file](./marathon.md#the-version-hot-file)), otherwise the published `standalone-skills-v<version>` bundle is missing the later PRs and no further build re-fires for that version.

## Version-freeze notes

While a version freeze holds, a PR leaves every version counter at its value on `main` and records the bump it would have made as a note file, `skills/assess/changes/<issue>.<counter>.md`, holding one or more markdown bullets. It lists the counter in its PR body under "Version bumps for the release PR". It never writes an `Unreleased` block into a history comment: each PR adds its own file, so two PRs that change the same counter do not conflict. The counter names and the file each one's history lives in are in [the notes README](../../skills/assess/changes/README.md).

`scripts/tests/test_fold_changes.py` fails on a note whose name is not `<issue>.<counter>.md`, whose counter is unknown, or whose text is not bullets, and on an `Unreleased` line left in a history comment.

The release PR folds the notes from the repository root:

```bash
make fold RELEASE=3.0.0
# with options: make fold RELEASE=3.0.0 FOLD_ARGS='--set artifact_schema=2.0.0 --date 2026-12-01'
```

`make fold` runs `scripts/fold_changes.py --release <RELEASE>` and then `make regen`, because the schemas, the catalog and the goldens stamp the counter values. The fold is not part of `make regen`: regen is safe to run after any conflict, while the fold deletes the notes and bumps each counter, once per release. Without `make`, run `uv run --locked python fold_changes.py --release 3.0.0` from `scripts/` and then the `regen` recipe; `fold_changes.py --check` validates the notes without writing.

For each counter with notes, the fold writes one history entry directly above the counter, `# <new> (<release> release): <n> changes since <old>.` followed by the notes in issue order, sets the counter to `<new>` and deletes the notes. `<new>` is the next minor version for `ARTIFACT_SCHEMA_VERSION`, the next integer for an integer counter, and `default-<date>` for the default maturity model (`--date`, default today). Pass `--set artifact_schema=2.0.0` (one `--set` per counter) to pick a different value. `fleet_row` has no default: with a `fleet_row` note present the fold stops until `--set fleet_row=<n>` is given, because the fleet history readers accept only the current row version. Setting the current value records the notes without a bump. A `--set` for a counter with no notes, or with a value the counter cannot take (`assess_method=abc`), stops the fold before it writes anything. `make regen` rewrites the generated files and goldens; the hand-written copies and pins do not move with it: `complexity-treemap.py`, the tests that name a counter's value, and `.claude-plugin/plugin.json` still move by hand in the same PR.

When `.claude-plugin/plugin.json` moves, move the GitLab gate pin with it in the same PR:

- [ ] `templates/assess-gate.yml`: the `toolkit_ref` input default (`v<version>`) and the include line in its header comment.
- [ ] `README.md` ("Use in GitLab CI"): the `include: remote:` line.

`skills/assess/tests/test_gitlab_gate.py::test_toolkit_pin_follows_the_plugin_version` fails until all three match `plugin.json`.

## Marketplace listing (manual, web-only)

The repo also publishes the [AI-Readiness Assess Gate action](https://github.com/marketplace/actions/ai-readiness-assess-gate). The listing's "latest version" does **not** advance with `gh release create`, and no API exists for the Marketplace flag. To refresh it: edit the release in the web UI, tick "Publish this Action to the GitHub Marketplace", update. Do this on marathon releases. Consumers are unaffected either way: their `uses:` pins and Dependabot bumps read git tags, not the listing.
