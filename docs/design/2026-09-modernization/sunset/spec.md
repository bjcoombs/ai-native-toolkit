# WS10 spec: Sunset

## Intent

Satisfies brief **R18** (removal half) of [../intent.md](../intent.md); tag `modernization-sunset` holds exactly one task, status `deferred` until the next MAJOR (3.0.0) is scheduled.

## Current state

Measured at `44bddbf`.

Deprecation Path rows in `../intent.md` whose "Removed in" column is `3.0.0`:

```
$ awk '/^## Deprecation Path/,/^## Programme sequence/' docs/design/2026-09-modernization/intent.md | rg -c '3\.0\.0'
5
$ awk '/^## Deprecation Path/,/^## Programme sequence/' docs/design/2026-09-modernization/intent.md | rg '3\.0\.0'
| Single plugin split (R1) | Symlink meta-plugin `ai-native-toolkit` keeping id, namespace, and every name; once-only notice | 3.0.0 (`renames -> null`) |
| `6hats` folded into `huddle` (R12) | Stub skill under the old name, forwards with the size argument | 3.0.0 |
| `docs/superpowers/` renamed (R13) | Redirect stub | 3.0.0 |
| `.github/claude-review-instructions.md` replaced (R8) | Three-line pointer | 3.0.0 |
| Assess helpers not folded (Out of scope) | Listed on the 3.0 list in the guide | 3.0.0 |
```

Whether a `remove-in:` marker exists in the tree today (the marker is planted by WS1/WS2/WS5, which have not merged yet, so it should not exist):

```
$ rg -n 'remove-in' --glob '!docs/design/**' .
[no output, exit 1]
```

Current plugin version:

```
$ jq -r '.version' .claude-plugin/plugin.json
1.56.0
```

Whether `renames` appears in `.claude-plugin/marketplace.json` today:

```
$ rg -n 'renames' .claude-plugin/marketplace.json
[no output, exit 1]
```

Two of the five items already exist on disk and will be live shims by the time this task runs: `.github/claude-review-instructions.md` (10,836 bytes today, replaced by `REVIEW.md` under R8/WS5) and `docs/superpowers/` (holds `README.md`, `plans/`, `specs/` today; WS5 merges those contents into the already-existing `docs/design/` - the directory this programme itself lives in - and leaves `docs/superpowers/README.md` as the redirect stub under R13).

## Design

This task is the deferred half of R18; the guide half (D3, `docs/migration-2.0.md`) ships with WS1. Nothing here executes before the next MAJOR - the task sits `deferred` in Task Master until then.

One removal action per Deprecation Path row measured above:

| Breadcrumb | Created by | Removal action |
|---|---|---|
| Symlink meta-plugin `ai-native-toolkit` (D5, R1/WS1) | WS1 | Add `renames: {"ai-native-toolkit": null}` to `.claude-plugin/marketplace.json`; delete `plugins/ai-native-toolkit/` (its `plugin.json` and every symlink); remove the `family-bump-requires-umbrella-bump` contract test, which has no umbrella version left to compare against once the meta-plugin is gone (`bump-requires-changelog` stays in force) |
| `6hats` stub (D4, R12/WS2) | WS2 | Delete `plugins/huddle/skills/6hats/`; remove the `6hats` allow-list entry from the `^[a-z][a-z-]*$` naming contract test |
| `docs/superpowers/` redirect stub (R13/WS5) | WS5 | Delete `docs/superpowers/README.md` - after WS5's merge into `docs/design/` that stub is all `docs/superpowers/` still holds - then remove the emptied directory once nothing else references it |
| `.github/claude-review-instructions.md` pointer (R8/WS5) | WS5 | Delete `.github/claude-review-instructions.md`; `REVIEW.md` is by then the sole review-policy source |
| Assess helpers fold (Out of scope, listed on the 3.0 list) | Not workstream-owned; deferred at programme design time because no runner-consumable transfer set exists for assess | Fold `plugins/assess/skills/assess-findings/` and `plugins/assess/skills/assess-pr/` into `plugins/assess/skills/assess/references/` |

The marketplace edit for the first row:

```json
{
  "renames": {
    "ai-native-toolkit": null
  }
}
```

None of the five removal actions touches a path marked by the floor today (`skills/marathon/SKILL.md`, `skills/pr-review-merge/SKILL.md`, `commands/tm.md`, `commands/issues.md`); by 3.0.0 the marked set is token-discovered per WS0 (R0), so this task carries no floor edit regardless.

Every surviving plugin bumps in the same PR, but not all onto the same line. The five non-`assess` family plugins go to `3.0.0`. `assess` takes a MINOR bump on its own 1.x line - from whatever `1.x` version it carries when this task runs (D2 puts it at `1.57.0` once the split lands; the Current state measurement above is the pre-split `1.56.0`). The programme's `3.0.0` is the other five families' line and never applies to `assess`.

A MAJOR bump for `assess` is rejected, per D2. D2 defines MAJOR by what the code keys off: `assess_core.py:388-390` treats a MAJOR change as a breaking change to the deterministic core, and `assess_gate.py:140` returns early from the regression compare when the diff is unreliable. So shipping `assess` as `2.0.0` would disarm `fail_on_regression` for every adopter on the version this ships and reset their trend history. Folding `assess-findings` and `assess-pr` into `references/` changes no code under `assess`'s `lib/` and no stats schema, so it cannot regress an adopter's metrics and is not MAJOR by that definition; a 3.0.0 calendar boundary is not the definition the gate reads.

The meta-plugin takes no bump. This task deletes `plugins/ai-native-toolkit/`, its `plugin.json`, and its `CHANGELOG.md`, so there is no manifest left to record a version in and no changelog entry to demand.

The guide's shim section - the part of `docs/migration-2.0.md` that lists active shims and their `remove-in` status - is deleted once every listed shim is gone; the guide's historical 1.x-to-2.0 mapping is untouched.

## Requirements

1. `rg -c 'remove-in: 3.0.0' --glob '!docs/design/**' .` returns 0 (or no match) after the task; `docs/design/2026-09-modernization/intent.md` and this spec keep the term as a historical record and are excluded from the count, same as the Current state command above.
2. `plugins/ai-native-toolkit/` does not exist: `test -d plugins/ai-native-toolkit && echo present || echo absent` prints `absent`.
3. `.claude-plugin/marketplace.json` carries `renames: {"ai-native-toolkit": null}` and no entry with `source: "./plugins/ai-native-toolkit"`: `jq '.renames, [.plugins[] | select(.source == "./plugins/ai-native-toolkit")]' .claude-plugin/marketplace.json` prints the rename and an empty array.
4. `plugins/huddle/skills/6hats/` does not exist, and no naming-contract allow-list entry names it: `test -d plugins/huddle/skills/6hats && echo present || echo absent` prints `absent`.
5. `docs/superpowers/` does not exist: `test -d docs/superpowers && echo present || echo absent` prints `absent`.
6. `.github/claude-review-instructions.md` does not exist: `test -f .github/claude-review-instructions.md && echo present || echo absent` prints `absent`.
7. `plugins/assess/skills/assess-findings/` and `plugins/assess/skills/assess-pr/` do not exist; their content is reachable under `plugins/assess/skills/assess/references/`.
8. Every plugin that survives this task has a version bump recorded in its `CHANGELOG.md` in the same PR (the `bump-requires-changelog` contract test from WS7/R10 is green for this PR's diff). The deleted meta-plugin is exempt: its `plugin.json` and `CHANGELOG.md` go with it, so it has neither a version to bump nor a changelog to record it in.
9. The `family-bump-requires-umbrella-bump` contract test is gone: a grep of `tests/` for its name returns no match. This PR bumps all six family plugins while deleting the umbrella they were compared against, so the test cannot hold and is removed in the same diff; `bump-requires-changelog` (Requirement 8) is untouched.
10. The shim section is gone from `docs/migration-2.0.md`: a grep for its heading returns no match, while the guide's historical 1.x-to-2.0 rows remain.
11. `GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest pytest tests/test_plugin_contract.py -q` is green.

## Verification

- `tests/test_plugin_contract.py::test_internal_links_resolve` and `::test_marketplace_entries_exist` (`plugin contract pytest` job) catch any dangling link left by the deletions and any marketplace entry whose `source` no longer resolves.
- `rg -c 'remove-in: 3.0.0' --glob '!docs/design/**' .` (Requirement 1) is the mechanical proof the enumeration is complete.
- `claude plugin validate .` zero warnings; `claude plugin validate plugins/<name> --strict` for each of the six remaining family plugins (the meta-plugin is gone, so it drops out of this loop).
- The WS7-introduced `bump-requires-changelog` contract test (fetches the base ref) is green for every plugin this PR touches. Its sibling `family-bump-requires-umbrella-bump` is deleted by this PR rather than kept green, per Requirement 9.
- The assess-helpers fold is not gated by `ab-equivalence`: the Out of scope entry in `../intent.md`'s programme source records that no runner-consumable transfer set exists for assess, so this fold ships as a plain move rather than a frozen one. It reaches no code under `assess`'s `lib/` and no stats schema, which is why it rides a MINOR (Design, citing D2) and not a MAJOR.

## Breadcrumbs

This workstream removes breadcrumbs and adds none: it deletes the five shims enumerated in Design (the meta-plugin, the `6hats` stub, the `docs/superpowers/` redirect, the `.github/claude-review-instructions.md` pointer, and the assess-helpers gap) and leaves no new pointer, stub, or `remove-in` marker in their place.

## Rollback

This PR is a pure deletion plus a version bump, docs and plugin trees only, no floor edit. `git revert` of the single commit restores every deleted file and the pre-bump versions, returning `main` to green. The programme cuts a version tag only after the post-merge self-test is green (the same pattern used for `v1.57.0`), so if the revert happens before that tag exists there is no immutable artefact to reconcile. If the tag has already been cut, the fix is a new forward PR that restores the shim content and bumps again - the tag itself cannot be un-cut.

## Sequencing

One PR, opened only after WS3, WS5, WS6, WS7, and WS9 have all merged (the programme dependency graph's `WS3, WS5, WS6, WS7, WS9 -> WS10`; WS1, WS2, WS4, and WS8 are satisfied transitively, since each of those five depends on one or more of them). The task stays `deferred` in Task Master until the next MAJOR is scheduled - it is the last task in the programme to leave that status.

It may not touch anything outside the five removal actions in Design, the version bumps they require, the `family-bump-requires-umbrella-bump` test removal named in that table's meta-plugin row, and the migration guide's shim-section deletion. It must not rename anything (WS2's territory, already merged), add a new component, touch `FLOOR.md`, `floor_check.py`, `floor_anchor.py`, or `floor.yml` (none of the five actions intersects a floor-marked path, per Design), or touch `action.yml` or a CI job `name:` (D7).
