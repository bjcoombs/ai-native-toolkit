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

## Marketplace listing (manual, web-only)

The repo also publishes the [AI-Readiness Assess Gate action](https://github.com/marketplace/actions/ai-readiness-assess-gate). The listing's "latest version" does **not** advance with `gh release create`, and no API exists for the Marketplace flag. To refresh it: edit the release in the web UI, tick "Publish this Action to the GitHub Marketplace", update. Do this on marathon releases. Consumers are unaffected either way: their `uses:` pins and Dependabot bumps read git tags, not the listing.
