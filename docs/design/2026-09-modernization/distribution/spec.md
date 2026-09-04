# WS7 spec: Distribution

## Intent

Satisfies briefs R10 (versioning) and R17 (open-standard standalone ZIPs) of the programme described in [../intent.md](../intent.md); parsed into Task Master tag `modernization-distribution`.

## Current state

Measured at 44bddbf on this checkout. Every number below sits next to the command that produced it.

### Size of what WS7 touches

```console
$ git rev-parse --short HEAD
44bddbf
$ wc -l scripts/standalone_skill_config.py scripts/build-standalone-skills.sh scripts/transform_skill.py .github/workflows/build-standalone-skills.yml .github/release.yml scripts/tests/test_integration.py
     300 scripts/standalone_skill_config.py
      92 scripts/build-standalone-skills.sh
     273 scripts/transform_skill.py
      73 .github/workflows/build-standalone-skills.yml
      43 .github/release.yml
     365 scripts/tests/test_integration.py
    1146 total
```

### `scripts/standalone_skill_config.py`: five ZIPs, one version, ten hard-coded paths

```console
$ rg -n '"source_dir"|"bundle_files"|plugin_json = |VERSION = |VERSION_SUFFIX' scripts/standalone_skill_config.py
30:    plugin_json = Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
34:VERSION = _plugin_version()
36:VERSION_SUFFIX = (
53:            + VERSION_SUFFIX
55:        "source_dir": "skills/assess",
136:        "bundle_files": {
150:            + VERSION_SUFFIX
152:        "source_dir": "skills/huddle",
192:        "bundle_files": {
213:            + VERSION_SUFFIX
215:        "source_dir": "skills/skill-forge",
233:        "bundle_files": {
248:            + VERSION_SUFFIX
250:        "source_dir": "skills/deslop",
277:            + VERSION_SUFFIX
279:        "source_dir": "skills/semantic-compress",
```

`SKILLS` has five entries: `assess`, `huddle`, `skill-forge`, `deslop`, `semantic-compress`. `_plugin_version()` at line 30 reads the single root manifest; line 34 freezes it into a module constant, and lines 53, 150, 213, 248 and 277 concatenate the resulting `VERSION_SUFFIX` into each description at import time.

```console
$ rg -n '^\s+"(references|hats)/' scripts/standalone_skill_config.py
137:            "references/assess-layer-scorer.md": "agents/assess-layer-scorer.md",
138:            "references/assess-findings.md": "skills/assess-findings/SKILL.md",
139:            "references/assess-pr.md": "skills/assess-pr/SKILL.md",
193:            "hats/white-hat.md": "agents/white-hat.md",
194:            "hats/red-hat.md": "agents/red-hat.md",
195:            "hats/black-hat.md": "agents/black-hat.md",
196:            "hats/yellow-hat.md": "agents/yellow-hat.md",
197:            "hats/green-hat.md": "agents/green-hat.md",
198:            "hats/blue-hat.md": "agents/blue-hat.md",
234:            "references/runner-prompt.md": "skills/ab-equivalence/references/runner-prompt.md",
```

Ten `bundle_files` sources, all repo-relative. Checked against the R1 plugin table, every one of them lands inside its own family: the three assess sources go to `assess`, the six hats to `huddle`, and `skills/ab-equivalence/references/runner-prompt.md` (line 234) to `skill-craft` alongside `skill-forge`. No `bundle_files` entry crosses a plugin boundary after the split, so the repoint is a prefix change and nothing more.

The five ZIPs span four of the six family plugins: `assess`, `huddle`, `deslop`, and `skill-craft` (two ZIPs). `gh-org` and `delivery` ship no ZIP.

### `scripts/build-standalone-skills.sh`: the config consumer

```console
$ rg -n 'source_dir|bundle_files|standalone_name|standalone_description' scripts/build-standalone-skills.sh
58:    out_zip = dest / f"{cfg['standalone_name']}.zip"
64:    bundle_files = {
66:        for dest_rel, src_rel in cfg.get("bundle_files", {}).items()
69:        skill_source_dir=repo_root / cfg["source_dir"],
71:        standalone_name=cfg["standalone_name"],
72:        standalone_description=cfg["standalone_description"],
75:        bundle_files=bundle_files,
```

The script resolves every config path against `repo_root` (argv[2], computed at line 14 as the parent of `scripts/`). It reads no version of its own.

### `.github/workflows/build-standalone-skills.yml`: one trigger, one version, one release

```console
$ rg -n 'branches:|paths:|plugin.json|standalone-skills-v|--latest|gh release' .github/workflows/build-standalone-skills.yml
5:    branches: [main]
6:    paths:
7:      - .claude-plugin/plugin.json
28:          old=$(git show "$BEFORE:.claude-plugin/plugin.json" 2>/dev/null \
31:          new=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json')).get('version',''))")
54:          VERSION=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")
55:          TAG="standalone-skills-v${VERSION}"
64:          if gh release view "$TAG" >/dev/null 2>&1; then
69:          gh release create "$TAG" \
72:            --latest=false \
```

One `paths:` trigger on the single root manifest (line 7); a before/after version compare at lines 28 and 31 that gates every later step; a third read at line 54 that names the tag `standalone-skills-v${VERSION}` (line 55). The release is created once and never edited (lines 64 to 67 skip an existing tag, because a GitHub immutable release permanently reserves it). `--latest=false` at line 72 keeps `releases/latest` pointing at the plugin release. The job holds `contents: write` (line 11) and already runs two SHA-pinned third-party actions inside it: `actions/checkout` (line 17) and `astral-sh/setup-uv` (line 42).

### `.github/workflows/release.yml` does not exist

```console
$ ls .github/workflows/release.yml
ls: .github/workflows/release.yml: No such file or directory
$ ls -1 .github/workflows/
assess-gate.yml
build-standalone-skills.yml
claude-review.yml
floor.yml
pr-lint.yml
tests.yml
```

There is no release workflow. Releases are cut by hand from `CLAUDE.md` "Release after a marathon" (lines 125 to 148), which runs `gh release create "v$VERSION" --generate-notes` and then `gh release edit` to prepend the ZIP-bundle pointer. The file the programme PRD calls `release.yml` is `.github/release.yml`, a 43-line GitHub release-notes category config (`changelog.categories` at lines 23 to 43). Its header comment at lines 11 to 19 carries a second copy of the release procedure, reading `.claude-plugin/plugin.json`.

### The leak assertion in `scripts/tests/test_integration.py`

```console
$ rg -n 'SKILL_DIR|CLAUDE_SKILL_DIR' scripts/tests/test_integration.py
67:            assert "SKILL_DIR" not in content, f"{name}: SKILL_DIR leaked"
```

One assertion, at line 67, inside `TestAssessBuild` (class opens at line 49). The bare substring `SKILL_DIR` already matches `CLAUDE_SKILL_DIR`, so the extension R17 asks for is coverage, not pattern: the assertion exists for the assess ZIP only and runs against markdown bodies, never against frontmatter keys.

### Version, tags, and what is not enforced

```console
$ jq -r .version .claude-plugin/plugin.json
1.56.0
$ git tag -l | rg -c '^standalone-skills-v'
126
$ git tag -l | rg -c '^v[0-9]'
62
$ git tag -l | wc -l
     189
```

126 standalone tags against 62 repo `v*` tags: the ZIP release fires on every version-bump push to `main`, while a `v*` tag is cut once per marathon. The remaining tag is the retired `standalone-skills-latest`.

```console
$ rg -c 'fetch-depth' .github/workflows/tests.yml || echo 'no match (rg exit 1)'
no match (rg exit 1)
$ ls CHANGELOG.md
ls: CHANGELOG.md: No such file or directory
```

The `plugin contract pytest` job (`tests.yml:49-59`) checks out at the default depth and fetches no base ref, so no test can compare the PR against its merge base. `tests/test_plugin_contract.py` asserts only that `plugin.json` parses and carries a version (`test_plugin_json_valid`, lines 186 to 188). Nothing asserts a bump, and there is no changelog to require a section in.

### Frontmatter fields present today

```console
$ for f in skills/*/SKILL.md; do echo "-- $f"; sed -n '1,/^---$/p' "$f" | rg '^[a-zA-Z_-]+:' -o; done | rg -v '^--' | sort | uniq -c
  12 description:
  12 name:
```

Every one of the twelve `skills/*/SKILL.md` files carries exactly `name` and `description`. `argument-hint`, `user-invocable`, `disable-model-invocation` and `allowed-tools` appear only under `commands/`, which ships no ZIP. The transform R17 specifies therefore strips nothing on today's tree; it is the guard that keeps WS3's R4 frontmatter from reaching a ZIP.

## Design

### Per-plugin semver (D2)

D2 is Decided and is not re-argued here. WS7 implements it: each `plugins/<name>/.claude-plugin/plugin.json` carries its own version, marketplace entries carry none, and repo `v*` tags track the `assess` plugin only.

Three mechanical consequences fall to WS7:

1. `_plugin_version()` cannot stay a module constant. Each `SKILLS` entry gains a `plugin` key naming its owning family, and the version suffix moves from import time to build time: `standalone_description` holds the description alone, and `build-standalone-skills.sh` appends `version_suffix(cfg["plugin"])` at line 72's call site. Five `+ VERSION_SUFFIX` concatenations are deleted; one function replaces them.
2. The workflow's three reads of `.claude-plugin/plugin.json` (lines 28, 31, 54) become a per-plugin loop over `plugins/*/.claude-plugin/plugin.json`, keyed by the plugin whose version moved in the pushed range.
3. The `source_dir` and `bundle_files` prefixes are **not** WS7's to change. R11 puts every Technical Context consumer in PR1's second commit, and these ten paths are rows in that table. WS7's first PR asserts the repoint landed rather than performing it.

Rejected: a single repo-wide `VERSION` kept for the ZIPs while plugins version independently. It reintroduces the lockstep D2 rejects, and it prints a version in the Skills UI that matches nothing a user can install.

### The same-PR bump contract test

A new test in `tests/test_plugin_contract.py` compares HEAD against the PR base:

- The base ref is fetched explicitly by the `plugin contract pytest` job. `tests.yml:53` gains `fetch-depth: 0`, and a step fetches `${{ github.base_ref }}` before pytest runs. The job `name:` literal stays `plugin contract pytest` (D7), so no branch-protection change is needed.
- For each `plugins/<x>/` whose diff against the base touches a component file (anything under `skills/`, `agents/`, `hooks/`, or the plugin `README.md`), `plugins/<x>/.claude-plugin/plugin.json` version must differ from the base version.
- A repo-level PR that touches no path under `plugins/` requires no bump. This spec PR and every other docs-only PR fall in that class.
- When the base ref is unavailable (a local run with no remote, a detached checkout), the test skips with a stated reason rather than passing silently.
- The skip is read per event, because `tests.yml:7-11` triggers on `push: branches: [main]` as well as `pull_request`, and `github.base_ref` is empty on a push. On a `pull_request` run a skip is a failure of the fetch step, which is why the fetch is a separate named step and not an inline `||`. On a `push` run the test skips with the reason `no base ref on a push run`, which is the expected outcome: the same diff was already checked by the `pull_request` run of the PR that produced the merge.

Rejected: a git hook or a `pr-lint.yml` title convention. Both live outside the required checks and neither can read the base tree.

### Family bump requires umbrella bump

The same test asserts that when any `plugins/<family>/.claude-plugin/plugin.json` version moves, `plugins/ai-native-toolkit/.claude-plugin/plugin.json` moves at the same level or higher, where level is MAJOR > MINOR > PATCH computed from the base and head triples. An assess MINOR requires at least an umbrella MINOR; an umbrella MAJOR satisfies a family PATCH.

The rule exists because D5's meta-plugin is a cache key: an install that keeps the umbrella sees a family change only when the umbrella's own version moves.

Rejected: deriving the umbrella version from the families (for example, summing or maxing). It makes the umbrella manifest a generated file that a human still has to commit, and it cannot express a change that is umbrella-only, such as the R14 notice hook.

### The `v*` tag-mismatch delete job

A new workflow `.github/workflows/tag-guard.yml` runs on `push: tags: ['v*']` with `contents: write`. It reads `plugins/assess/.claude-plugin/plugin.json` at the tagged commit and compares it to the tag minus the leading `v`. On a mismatch it deletes the ref and exits non-zero with the two versions in the message.

The window matters: a tag is cheap to delete until a release is attached, after which GitHub reserves it permanently. The job runs on the tag push, which precedes `gh release create` in the manual procedure, so the guard fires while deletion is still possible. `CLAUDE.md` "Release after a marathon" moves to `plugins/delivery/skills/marathon/references/release-after-marathon.md` under R7 and reads the assess manifest, never the umbrella's.

PR7.2 lands the job, and it lands after `v1.57.0` already exists. Every WS7 PR waits on PR1, and PR1 ends by tagging `v1.57.0` on its last commit ([../intent.md](../intent.md), programme sequence step 2), so the tag namespace the guard first runs against holds `v1.57.0` and the 62 earlier `v*` tags, none of which it has ever checked. `v1.57.0` is cut by hand before the guard exists and is covered instead by PR1's post-merge self-test. The guard is forward-only, firing on tag pushes: the first real tag it checks is the next `v*` after `v1.57.0`, and the only tag it checks before that is the throwaway `v0.0.1` of its own recorded run.

Rejected: asserting the tag inside `build-standalone-skills.yml`. That workflow triggers on manifest pushes, not tag pushes, and would fire after the release exists.

### Per-plugin standalone releases

`build-standalone-skills.yml` changes in four places:

| Line today | Today | After |
|-----------|-------|-------|
| 6-7 | `paths: [.claude-plugin/plugin.json]` | `paths: ['plugins/*/.claude-plugin/plugin.json']` |
| 22-38 | one before/after compare on the root manifest | one compare per plugin manifest, emitting the list of plugins whose version moved |
| 44-46 | `build-standalone-skills.sh` with no argument | one invocation per changed plugin: `--dest dist/standalone-skills/<plugin>` plus the skill names that plugin owns, so each plugin's ZIPs land in a directory of their own |
| 48-73 | one release `standalone-skills-v${VERSION}` | one release per changed plugin that owns at least one ZIP, `standalone-<plugin>-v<version>`, assets globbed from `dist/standalone-skills/<plugin>/*.zip` and named `<skill>-v<version>.zip` |

The output directory is per plugin because `build-standalone-skills.sh:15` defaults `DEST` to one shared `dist/standalone-skills` for every invocation. Building each plugin into that shared directory and then globbing `*.zip` per release would attach the earlier plugins' ZIPs to the later plugin's release, and by the Rollback section below an immutable release cannot be corrected afterwards. The script already takes `--dest`, so each invocation writes to `dist/standalone-skills/<plugin>/` and each release globs that subdirectory alone. Requirement 11 is then structurally true rather than dependent on run order.

A plugin gets a release only if at least one `SKILLS` entry in `scripts/standalone_skill_config.py` names it in the `plugin` key added above. The family-bump-requires-umbrella-bump rule moves `plugins/ai-native-toolkit/.claude-plugin/plugin.json` on every family bump, so the umbrella always matches the new `paths` glob, and the Current state above records that the five ZIPs span four families: `gh-org` and `delivery` own none, and the umbrella owns none either. Without the exclusion, every publishing push would also attempt `standalone-ai-native-toolkit-v<version>` with a glob matching nothing, which fails the step, because an unmatched glob reaches `gh` as a literal path it cannot find.

`--latest=false` is kept on every release so `releases/latest` stays the action release that adopters' `uses:` pins resolve against. The existing-release skip (lines 64 to 67) is kept per plugin, unchanged in intent: an immutable release is never overwritten.

Asset naming changes from `<skill>.zip` to `<skill>-v<version>.zip` because a `skill-craft` release carries two ZIPs whose versions are the plugin's, and a downloaded file with no version in its name cannot be told apart from the previous one in a Downloads folder.

Rejected: keeping one combined `standalone-skills-v<version>` release keyed on the umbrella version. It publishes five ZIPs on every family bump, four of them byte-identical to the previous release, and it makes the umbrella version the thing users read in the Skills UI rather than the version of the skill they installed.

### The Agent Skills frontmatter transform

`transform_skill.override_frontmatter` (line 51) today rewrites `name` and `description` in place and leaves every other key. It gains a whitelist pass. Field by field:

| Source field | Agent Skills spec field | In the ZIP | Rule |
|--------------|------------------------|-----------|------|
| `name` | yes | kept | rewritten to `standalone_name` (`transform_skill.py:63`) |
| `description` | yes | kept | rewritten to `standalone_description` plus the build-time version suffix (`transform_skill.py:64-70`) |
| `license` | yes | added | copied from the owning plugin's `plugin.json` `license` field |
| `compatibility` | yes | added | states the runtime needs of that ZIP, for example whether it needs a terminal for its scripts |
| `metadata` | yes | added | `plugin` (owning plugin name) and `version` (that plugin's version) |
| `allowed-tools` | yes | stripped | spec-legal but inert off Claude Code, and a `${CLAUDE_SKILL_DIR}` value would ship as a literal string |
| `argument-hint` | no | stripped, text moved into the body | inserted as `**Arguments:** <text>` immediately after the body's first heading, so a reader keeps the usage line |
| `user-invocable` | no | stripped | controls the Claude Code slash menu; no meaning off-platform |
| `disable-model-invocation` | no | stripped | same |
| any other key | no | build fails | an unknown key is an authoring mistake, not something to silently drop |

The failure mode for an unknown key is deliberate. Silently dropping is how a field that matters reaches a user's Skills UI missing, and the same choice is what `quick_validate.py` makes on the other side of the ZIP boundary.

Rejected: a denylist of the four known non-spec fields. It passes any future Claude Code frontmatter field straight into the ZIP, which is the bug R17 exists to close.

### `quick_validate.py` as a pinned dependency

`build-standalone-skills.yml` gains a validation step between the build and the release: unzip each built ZIP into a temp directory, then run `quick_validate.py` from `anthropics/skills` over it, failing the job on the unexpected-key error.

- Acquisition: a second `actions/checkout` step with `repository: anthropics/skills`, `ref: <full 40-character commit SHA>`, `sparse-checkout: skills/skill-creator/`, `persist-credentials: false`, into a path outside the workspace tree that is zipped.
- PyYAML is installed through `uv run --with 'pyyaml==<x.y.z>'`. The rest of the pipeline resolves `--with` dependencies unpinned, but this one runs inside the job holding `contents: write`, so it is pinned to an exact version and moves only in a PR that records the change, under the same rule as the SHA below.
- The pin is a full SHA, never a tag or branch, and the PR that introduces or moves it records what changed between the old and new SHA in its body. The job already runs two SHA-pinned third-party actions, `actions/checkout` and `astral-sh/setup-uv`; `quick_validate.py` joins them under the same rule and is the first third-party code the job executes from a non-Actions source, so the pin is reviewed the way a dependency is.
- The step runs before `gh release create`. A ZIP that fails validation blocks its own release rather than shipping and being fixed afterwards, which an immutable release cannot be.

Rejected: vendoring a copy of `quick_validate.py` into `scripts/`. It removes the pin review at the cost of a silent fork that drifts from the spec it validates against.

Rejected: running the validator in the `scripts/ pytest` job instead. That job holds `contents: read` and is the right place for a local check, but it does not gate the release path, and the release path is where an unvalidated ZIP becomes permanent.

## Requirements

Each requirement is verifiable by the command, test, or recorded verdict named with it.

1. Every `SKILLS` entry declares a `plugin` key naming its owning family plugin, and `version_suffix(plugin)` reads `plugins/<plugin>/.claude-plugin/plugin.json`. Verified by a unit test in `scripts/tests/test_transform.py` asserting the suffix for `assess` reports the assess version and the suffix for `deslop` reports the deslop version, and that the two differ.
2. `scripts/standalone_skill_config.py` contains no module-level `VERSION` constant and no `+ VERSION_SUFFIX` concatenation. Verified by `rg -c 'VERSION_SUFFIX' scripts/standalone_skill_config.py` reporting no match.
3. The build produces one ZIP per `SKILLS` entry with the owning plugin's version in its description suffix. Verified by `bash scripts/build-standalone-skills.sh` followed by the assertions in requirement 1's test.
4. The `plugin contract pytest` job checks out with `fetch-depth: 0` and fetches the PR base ref in a named step before pytest. Verified by `rg -n 'fetch-depth' .github/workflows/tests.yml` and by the job's own log.
5. A component change under `plugins/<x>/` without a version move in `plugins/<x>/.claude-plugin/plugin.json` fails `tests/test_plugin_contract.py`. Verified by the test's own fixture cases, which construct both the bumped and unbumped diff shapes.
6. A family version move without a same-or-higher move in `plugins/ai-native-toolkit/.claude-plugin/plugin.json` fails the same test. Verified by fixture cases covering PATCH under MINOR (fails) and MAJOR over MINOR (passes).
7. A PR touching nothing under `plugins/` requires no bump. Verified by a fixture case built from a docs-only diff.
8. A version move in `plugins/<x>/.claude-plugin/plugin.json` without a new section for that version in `plugins/<x>/CHANGELOG.md` fails the contract test (R15). The rule applies to a plugin that has a `CHANGELOG.md` and skips with a stated reason for one that does not, because WS5 ([../knowledge/spec.md](../knowledge/spec.md)) owns creating the per-plugin changelogs under brief R15 and no WS7 PR creates one. The rule therefore covers every plugin only once WS5 has landed. Verified by fixture cases in the same test module covering the present-with-section, present-without-section, and no-changelog-yet shapes.
9. `.github/workflows/tag-guard.yml` deletes a pushed `v*` tag whose version does not match `plugins/assess/.claude-plugin/plugin.json` at that commit, and exits non-zero. Verified by a recorded run against a throwaway `v0.0.1` tag, pasted into the PR body, showing the deletion and the failure.
10. `build-standalone-skills.yml` triggers on `plugins/*/.claude-plugin/plugin.json` and on `workflow_dispatch` only. Verified by `rg -n -A 6 '^on:' .github/workflows/build-standalone-skills.yml`.
11. Each release is named `standalone-<plugin>-v<version>`, is created with `--latest=false`, and carries only that plugin's ZIPs, named `<skill>-v<version>.zip`, because the build writes them to `dist/standalone-skills/<plugin>/` and the release step globs that subdirectory alone. Verified by `rg -n 'standalone-|--latest|--dest' .github/workflows/build-standalone-skills.yml` and by the first real release the workflow cuts.
12. Every shipped `SKILL.md` frontmatter contains only keys from the Agent Skills six, and contains none of `argument-hint`, `user-invocable`, `disable-model-invocation`, `allowed-tools`. Verified by a test in `scripts/tests/test_integration.py` that parses each ZIP's frontmatter and compares its key set.
13. A source `SKILL.md` carrying `argument-hint` produces a ZIP whose body contains the hint text and whose frontmatter does not. Verified by a fixture-driven test in `scripts/tests/test_transform.py`.
14. An unrecognised frontmatter key in a source `SKILL.md` fails the build with a message naming the key and the file. Verified by a fixture-driven test in `scripts/tests/test_transform.py`.
15. The `CLAUDE_SKILL_DIR` leak assertion covers every built ZIP, not the assess ZIP alone. Verified by parametrizing the assertion over all `SKILLS` entries in `scripts/tests/test_integration.py`.
16. Every built ZIP passes `quick_validate.py` from `anthropics/skills` at the pinned SHA, and the step runs before any `gh release create`. Verified by the workflow file's step order and by the job log of the first release run.
17. The `quick_validate.py` checkout pins a full 40-character commit SHA with `persist-credentials: false`, and the validation step pins PyYAML to an exact version. Verified by `rg -n -A 6 'anthropics/skills' .github/workflows/build-standalone-skills.yml` and by `rg -n 'pyyaml==' .github/workflows/build-standalone-skills.yml`.
18. No release is attempted for a plugin that no `SKILLS` entry names in its `plugin` key, so an umbrella-only bump, a `gh-org` bump, or a `delivery` bump publishes nothing. Verified by a unit test over `SKILLS` asserting the releasable set is exactly the four families that own a ZIP, and by the job log of the first family bump after WS7 merges, which shows no release attempted for `ai-native-toolkit`.

## Verification

- `scripts/tests/test_transform.py` covers the per-plugin version read (requirement 1), the `argument-hint` body move (13), and the unknown-key failure (14). These are unit fixtures, not full builds, so they run in the `scripts/ pytest` required check on every PR.
- `scripts/tests/test_integration.py` covers the shipped frontmatter key set (12) and the extended leak assertion (15) by building every ZIP in a temp directory, the same fixture pattern `TestAssessBuild` and its four siblings already use.
- `tests/test_plugin_contract.py` covers the bump rules (5, 6, 7) and the changelog rule (8) against constructed base-versus-head fixtures, so the assertions are exercised without needing a real PR. WS7 proves only that the changelog rule fires correctly on each of its three shapes; that every plugin has a `CHANGELOG.md` is WS5's to prove, under [../knowledge/spec.md](../knowledge/spec.md). The `plugin contract pytest` job proves the base fetch works on a real PR (4).
- `.github/workflows/tag-guard.yml` is proved by a recorded run, not by a unit test: a throwaway `v0.0.1` tag is pushed against a commit whose assess manifest reads something else, and the run log plus `git ls-remote --tags` afterwards go into the PR body (9).
- `build-standalone-skills.yml` is proved by its first real invocation. The `workflow_dispatch` path runs the build, the validator, and a release create against a version that already has a release, so the idempotent skip (lines 64 to 67 today) exercises without publishing. The publishing path is proved by the first family bump after WS7 merges (11, 16, 18).
- No `ab-equivalence` run is required. WS7 changes no `SKILL.md` body and no agent instruction: the frontmatter transform operates on the ZIP, and the source tree it reads is unchanged.

## Breadcrumbs

WS7 leaves no code shim. The three artefacts it strands are historical and stay reachable:

| Artefact | Status after WS7 | Row in `docs/migration-2.0.md` |
|----------|-----------------|-------------------------------|
| 126 `standalone-skills-v*` releases | Kept, immutable, never re-cut | "Standalone ZIP releases are now `standalone-<plugin>-v<version>`; releases up to `standalone-skills-v1.56.0` stay where they are" |
| `standalone-skills-latest` tag | Kept, already retired before WS7 | Not listed; it predates this programme |
| ZIP asset names `<skill>.zip` | Replaced by `<skill>-v<version>.zip` | "A ZIP downloaded before 2.0 has no version in its filename; the version is in the skill's description in the Skills UI" |

No `remove-in: 3.0.0` item originates in WS7. The migration guide rows above are informational and stay past 3.0, because the old releases stay past 3.0.

## Rollback

Every WS7 PR is revertible by `git revert` with no state to unwind, with one exception stated below.

- The contract-test PR, the tag-guard PR, and the frontmatter-transform PR touch only test files, workflow files, and `scripts/`. Reverting each returns `main` to the previous required-check set, and the required check names are unchanged throughout (D7), so no branch-protection edit is needed on the way out.
- The workflow PR that repoints the trigger and the release naming is revertible until it publishes. Per the Spec contract, a GitHub release and its tag are immutable once created: a `standalone-<plugin>-v<version>` release that has been cut cannot be un-shipped by reverting the workflow. The mitigation is ordering, not reversal. The validator step runs before `gh release create`, and the workflow's first exercise is a `workflow_dispatch` against an already-released version, where the existing-release skip fires and nothing is published. The first publishing run happens only after that dispatch is green.
- The same immutability governs `v*`. `tag-guard.yml` exists so a mismatched `v*` tag is deleted in the window before a release attaches to it. If a mismatched tag has already backed a release, the tag is spent: the correct move is a new tag at the right version, and the guard's failure message says so rather than implying the tag can be reclaimed.

## Sequencing

Five PRs, in order. All of them depend on PR1 (WS1) having merged, because every path they assert against lives under `plugins/`.

1. **PR7.1 - version contract.** Adds `fetch-depth: 0` and the base-ref fetch step to the `plugin contract pytest` job; adds the same-PR bump test, the family-bump-requires-umbrella-bump test, and the changelog-section test to `tests/test_plugin_contract.py`. May not touch `scripts/`, `build-standalone-skills.yml`, or any job `name:` literal. The changelog-section test lands in the conditional form requirement 8 states, so merging it cannot fail a later PR against a `CHANGELOG.md` that WS5 has not created yet. PR7.1 does not wait on WS5, and WS5 does not wait on PR7.1.
2. **PR7.2 - tag guard.** Adds `.github/workflows/tag-guard.yml` and the recorded throwaway-tag run. May not touch `tests.yml` or the standalone pipeline. It lands after `v1.57.0`, which PR1 cuts by hand, so the guard's first real subject is the next `v*` tag after `v1.57.0`.
3. **PR7.3 - per-plugin versions in the pipeline.** Replaces `VERSION`/`VERSION_SUFFIX` with a per-plugin `version_suffix()`, adds the `plugin` key to each `SKILLS` entry, and repoints the workflow trigger, version compare, and release naming to per-plugin. May not touch `source_dir` or `bundle_files` (PR1 owns those rows) and may not touch the frontmatter transform.
4. **PR7.4 - Agent Skills frontmatter.** Adds the whitelist pass to `transform_skill.override_frontmatter`, the `license`/`compatibility`/`metadata` fields, the `argument-hint` body move, the unknown-key failure, and the parametrized `CLAUDE_SKILL_DIR` leak assertion. May not touch the workflow.
5. **PR7.5 - ZIP validation.** Adds the pinned `anthropics/skills` sparse checkout and the `quick_validate.py` step between build and release, with the SHA review in the PR body. May not touch `scripts/`. Last, because it validates what PR7.4 produces.

PR7.1 and PR7.2 are independent of each other and of PR7.3 to PR7.5; PR7.3, PR7.4, and PR7.5 are strictly ordered. No PR in this workstream touches `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, `FLOOR.md`, or `action.yml`.
