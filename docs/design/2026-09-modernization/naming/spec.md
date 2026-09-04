# WS2 spec: Naming

## Intent

Satisfies naming brief **R12** of [../intent.md](../intent.md), parsed into tag `modernization-naming`.

## Current state

Measured at `44bddbf`, from the repo root of this checkout.

Every current skill directory already matches the naming pattern the contract test will enforce:

```
$ find skills -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
ab-equivalence
assess
assess-findings
assess-pr
deslop
ghreport
ghsync
huddle
marathon
pr-review-merge
semantic-compress
skill-forge
```

```
$ find skills -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | rg -v '^[a-z][a-z-]*$'
$ echo rc=$?
rc=1
```

`rc=1` means the filter matched nothing: all 12 current skill directories already satisfy `^[a-z][a-z-]*$`. `skills/README.md` is a file, not a directory, so it never enters this set (it is `test_plugin_contract.py`'s `skill_dirs()`, which requires a `SKILL.md` sibling; R3 covers `README.md`'s removal separately).

The one name the pattern rejects is the one this workstream adds. `commands/6hats.md` becomes a skill directory named `6hats` under WS1's move (R2), and a leading digit fails the pattern:

```
$ echo -n "6hats" | rg -q '^[a-z][a-z-]*$'; echo "rc=$?"
rc=1
```

`commands/6hats.md`'s current frontmatter, already shaped like a skill:

```
$ sed -n '1,6p' commands/6hats.md
---
name: 6hats
disable-model-invocation: true
description: "Six Thinking Hats Analysis"
argument-hint: "<topic or problem to analyze>"
---
```

`huddle`'s current frontmatter has no `argument-hint` (it is not user-invocable with a structured argument today; team size is inferred from the topic text in the SKILL.md body):

```
$ rg -n 'argument-hint' skills/huddle/SKILL.md; echo "rc=$?"
rc=1
```

`ghreport/scripts/ghreport.sh`'s sibling lookup, the reason `ghsync` cannot be renamed (D4):

```
$ sed -n '281,286p' skills/ghreport/scripts/ghreport.sh
# ---- discovery (delegated to ghsync --porcelain) ---------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHSYNC_SH="$SCRIPT_DIR/../../ghsync/scripts/ghsync.sh"
if [ ! -f "$GHSYNC_SH" ]; then
    GHSYNC_SH="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills}/ghsync/scripts/ghsync.sh"
fi
```

`ghsync` and `ghreport` already carry their final imperative names (`name: ghsync`, `name: ghreport`), so no change reaches either directory or frontmatter under this workstream.

`test_plugin_contract.py` has no directory-naming assertion today - `skill_dirs()` (`tests/test_plugin_contract.py:69-72`) discovers by `SKILL.md` presence only, with no pattern check on the directory name itself:

```
$ rg -n 'def test_' tests/test_plugin_contract.py
114:def test_skill_frontmatter
122:def test_skill_has_trigger_clause
128:def test_no_placeholder_tokens
134:def test_internal_links_resolve
149:def test_use_the_skill_references_resolve
156:def test_subagent_types_resolve
165:def test_no_leaked_tool_envelope_tags
174:def test_no_conflict_markers
186:def test_plugin_json_valid
191:def test_marketplace_entries_exist
203:def test_team_skills_excluded_from_standalone
```

## Design

Cites D4 (skill naming, Decided): keep the imperative names; fold `6hats` into `huddle` as a size argument; `ghsync`/`ghreport` unchanged. D4 already records why: the best-practices guide only requires consistency, met per plugin; a `disable-model-invocation` skill cannot carry a working forwarding alias (skills.md: "Claude Code blocks the call"), so a rename-with-breadcrumb pattern fails for six of the eighteen candidate renames; `ghsync`'s name is load-bearing for `ghreport.sh:283`'s sibling lookup. This spec does not re-argue D4; it fixes the one mechanical consequence D4 leaves open - `6hats` is the sole surviving name that needs a breadcrumb, and the sole name that fails a plain lowercase-kebab pattern - and states the shape of that breadcrumb and its test.

Three choices follow from D4, not alternatives to it:

1. **`6hats` survives as a real, selectable skill, not a redirect comment.** A `disable-model-invocation: true` skill has no model-routed invocation to intercept, so the only honest breadcrumb is a skill directory a user can still type `/6hats` against, whose body forwards to `huddle`'s protocol with the team size fixed at 1. This is what `commands/6hats.md` already does today (`git log`-visible: "This is an alias for `/huddle` with team size 1"); WS2 changes only its home (a skill directory) and its `remove-in` breadcrumb, not its forwarding behaviour. Rejected: deleting `commands/6hats.md` outright in PR1 - D4 already rejects this (silent break, no breadcrumb); rewriting it as a doc pointer - unreachable by a user who still types `/6hats`.
2. **The naming contract test allow-lists `6hats` instead of loosening the pattern.** Loosening `^[a-z][a-z-]*$` to admit a leading digit would silently accept a future skill named like a version string or a typo'd numeral; a one-entry allow-list keyed to a `remove-in` version keeps the pattern strict for every name added after this spec and self-documents why the one exception exists. Rejected: a permissive pattern (`^[a-z0-9][a-z0-9-]*$`) - passes the test but hides the debt the Deprecation Path already tracks.
3. **`huddle` reads team size from `$0` behind an `argument-hint`, rather than continuing to infer it from prose.** `argument-hint: "[solo|2|3|5|8] <topic>"` documents the contract in `/help` and the skill picker; `$0` (skill frontmatter's positional-argument substitution, distinct from `$ARGUMENTS`) gives the skill a token to check without parsing the whole topic string. The Fibonacci sizes are those the current `huddle/SKILL.md` Team size section already enumerates (`skills/huddle/SKILL.md:92-97`); this is a frontmatter and argument-parsing addition, not a change to the sizing table. When `$0` is absent or not one of `solo|2|3|5|8`, `huddle` keeps its current behaviour: it infers a size from the topic per the existing Team size guidance. This choice is also **R16**'s listed candidate ("the `huddle` size argument replacing `6hats`") in its frontmatter shape only; R16's own PR (WS8, gated by `skill-forge`) owns the behavioural change of actually retiring `6hats`'s independent invocation surface. WS2 ships the argument-hint and the `6hats` breadcrumb together in PR1; it does not remove `6hats`.

## Requirements

1. No skill directory present before PR1 changes name. Verifiable: `sorted(d.name for d in skill_dirs())` (current form, `tests/test_plugin_contract.py:69-72`, re-parametrized over `plugins/*/skills` post-move per WS1) contains all 12 names listed in Current state, unchanged, after PR1.
2. `6hats` exists as `plugins/huddle/skills/6hats/SKILL.md`, keeping `disable-model-invocation: true` and its existing forwarding body, and gains a `remove-in: 3.0.0` breadcrumb line. Verifiable: `test_skill_frontmatter` and `test_skill_has_trigger_clause` (or an equivalent frontmatter check for `disable-model-invocation` skills, which are exempt from the TRIGGER requirement since they are never model-routed) pass for `6hats`; a `rg -n 'remove-in: 3.0.0' plugins/huddle/skills/6hats/SKILL.md` finds the line.
3. `plugins/huddle/skills/huddle/SKILL.md` frontmatter gains `argument-hint: "[solo|2|3|5|8] <topic>"`; the skill body documents reading the size token from `$0`, falling back to prose-inferred sizing when `$0` is absent or unrecognised. Verifiable: `rg -n 'argument-hint' plugins/huddle/skills/huddle/SKILL.md` finds the line; `rg -n '\$0' plugins/huddle/skills/huddle/SKILL.md` finds the size-read logic.
4. `ghsync` and `ghreport` keep their directory names, `name:` frontmatter values, and every existing path reference between them unchanged by this workstream. Verifiable: `git diff` for this workstream's scope touches no file under `gh-org/` other than the plugin move itself (WS1's concern, not WS2's).
5. A new contract test, `test_skill_directory_naming`, asserts every skill directory matches `^[a-z][a-z-]*$` except a `NAMING_ALLOWLIST = {"6hats"}` constant carrying a comment pointing at `remove-in: 3.0.0`. Verifiable: `pytest tests/test_plugin_contract.py -k test_skill_directory_naming` passes on the post-move tree and fails if `NAMING_ALLOWLIST` is deleted while `6hats` still exists.
6. Nothing else renames. Verifiable: the 12 names in Current state plus `6hats` are the complete set of skill directory names after PR1; every other former `commands/*.md` file (`fix-develop`, `fix-pr`, `issues`, `tm`, `tm-marathon-config-example`, `understand`) keeps the same base name it has today when it becomes a skill under R2.

## Verification

`test_skill_directory_naming` (new, added to `tests/test_plugin_contract.py` in PR1's second commit per D8) is the deterministic gate for this workstream's own change: it runs in the required `plugin contract pytest` job on every PR from PR1 onward.

The frontmatter and behavioural side of the change - `6hats`'s unchanged forwarding body and `huddle`'s new `argument-hint`/`$0` read - rides PR1's `ab-equivalence` freeze (the gate the spec contract names for pure moves; see the WS1 spec's Verification section for the transfer set it runs). WS2 introduces no new transfer set of its own: the `$0` size-read path is exercised by WS4's 8-case huddle transfer set (D9) once that set exists, not by this spec.

## Breadcrumbs

- `6hats`: `disable-model-invocation: true` stub under `plugins/huddle/skills/6hats/`, forwards to `huddle` with the size fixed at solo, `remove-in: 3.0.0`. Already recorded in [../intent.md](../intent.md)'s Deprecation Path table ("`6hats` folded into `huddle` (R12)").
- `docs/migration-2.0.md` (created under WS1's R18 guide skeleton) gains a row: old path `commands/6hats.md` / `/6hats` -> new path `plugins/huddle/skills/6hats/SKILL.md`, still invocable as `/6hats` or `/huddle:6hats`, superseded by `/huddle [solo|2|3|5|8] <topic>`, `remove-in: 3.0.0`.
- No other breadcrumb: `ghsync`/`ghreport` need none (no rename); every other command-to-skill conversion is R2's breadcrumb-free move (a file in `commands/` loads as a skill, per the Deprecation Path's "never" row), not R12's.

## Rollback

WS2 carries no independent PR or floor edit; it ships inside PR1's second commit (Design, point 1 and 3, and Sequencing below). Reverting PR1 reverts both commits together: the bare `git mv` (first commit) and the consumer updates including the `6hats` stub creation, the `huddle` `argument-hint` addition, and the new `test_skill_directory_naming` test (second commit). Because PR1 is a pure move plus consumer update with zero floor edits (D8), the revert is a clean inverse - `commands/6hats.md` and `skills/huddle/SKILL.md` return to the Current state shown above, and `main` returns to green under the same required checks. Per the programme PRD's Rollback contract: the `v1.57.0` tag rides the last commit of PR1 and is cut only after the post-merge self-test is green, so a rollback before that point un-ships no immutable tag or release.

## Sequencing

Lands inside **PR1** (WS1 + WS2), in the second commit - the consumer-update commit that follows PR1's first, bare `git mv` commit (D8) - since the `6hats` stub body and the `huddle` `argument-hint` are content changes, not a path move. PR1 may not touch `floor_check.py`, `floor_anchor.py`, `floor.yml`, or `FLOOR.md`, and no CI job `name:` literal changes (D7); WS2 adds no exception to either constraint.

Nothing else renames. R12 is the complete naming scope for the programme: no other skill, agent, or command gains a new base name in PR1 or in any later workstream PR. Every subsequent workstream (WS3 through WS10) changes frontmatter, body content, hooks, or distribution shape, never a component's name.
