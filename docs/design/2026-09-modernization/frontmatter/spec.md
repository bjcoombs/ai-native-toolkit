# WS3 spec: Frontmatter and agents

## Intent

WS3 satisfies briefs R4 (skill frontmatter adoption) and R5 (agent frontmatter adoption) of the programme recorded in [intent.md](../intent.md), indexed in [README.md](../README.md), under Task Master tag `modernization-frontmatter`.

## Current state

Measured at 44bddbf, from the worktree root. Every number below carries the command that produced it and that command's real output.

### Skill and command frontmatter keys

```
$ for f in skills/*/SKILL.md commands/*.md; do printf '%-44s' "$f"; awk '/^---$/{c++; if(c==2) exit; next} c==1 && /^[a-z-]+:/{sub(/:.*/,""); printf "%s ", $0}' "$f"; echo; done
skills/ab-equivalence/SKILL.md              name description
skills/assess-findings/SKILL.md             name description
skills/assess-pr/SKILL.md                   name description
skills/assess/SKILL.md                      name description
skills/deslop/SKILL.md                      name description
skills/ghreport/SKILL.md                    name description
skills/ghsync/SKILL.md                      name description
skills/huddle/SKILL.md                      name description
skills/marathon/SKILL.md                    name description
skills/pr-review-merge/SKILL.md             name description
skills/semantic-compress/SKILL.md           name description
skills/skill-forge/SKILL.md                 name description
commands/6hats.md                           name disable-model-invocation description argument-hint
commands/fix-develop.md                     name disable-model-invocation description argument-hint
commands/fix-pr.md                          name disable-model-invocation description argument-hint
commands/issues.md                          name disable-model-invocation description argument-hint
commands/README.md
commands/tm-marathon-config-example.md      name disable-model-invocation description
commands/tm.md                              name disable-model-invocation description argument-hint
commands/understand.md                      name disable-model-invocation description argument-hint
```

No skill declares `user-invocable`, `argument-hint`, `allowed-tools`, or `disable-model-invocation`. Every optional field in the repo today sits on a command file:

```
$ rg -n '^(user-invocable|allowed-tools|argument-hint|disable-model-invocation):' skills/ commands/ agents/ | sort
commands/6hats.md:3:disable-model-invocation: true
commands/6hats.md:5:argument-hint: "<topic or problem to analyze>"
commands/fix-develop.md:3:disable-model-invocation: true
commands/fix-develop.md:5:argument-hint: "[branch] (optional - defaults to the repo's default branch)"
commands/fix-pr.md:3:disable-model-invocation: true
commands/fix-pr.md:5:argument-hint: "[pr-number] (optional - derives from current branch if omitted)"
commands/issues.md:3:disable-model-invocation: true
commands/issues.md:5:argument-hint: [scope-label] (optional - narrows which open issues are considered; default: all open issues)
commands/tm-marathon-config-example.md:3:disable-model-invocation: true
commands/tm.md:3:disable-model-invocation: true
commands/tm.md:5:argument-hint: [tag [task-id] | feature description] (optional - derives context from worktree if omitted)
commands/understand.md:3:disable-model-invocation: true
commands/understand.md:5:argument-hint: "<thing to understand>"
```

`allowed-tools` appears nowhere: the command above returns no line for it.

### Description length and TRIGGER clause

`marathon` and `pr-review-merge` use a folded block scalar, so the length is the folded value joined with single spaces.

```
$ for f in skills/*/SKILL.md commands/*.md; do
    awk -v F="$f" '/^---$/{c++; if(c==2) exit; next}
      c==1 && /^description:/{sub(/^description:[ ]*/,""); if($0 ~ /^(>|>-|\|)$/){d="";g=1} else {d=$0;g=0}; inD=1; next}
      c==1 && inD && g && /^[ ]+/{sub(/^[ ]+/,""); d=(d==""?$0:d" "$0); next}
      c==1 && inD && /^[a-zA-Z]/{inD=0}
      END{if(d!="") printf "%4d chars  TRIGGER=%-3s %s\n", length(d), (d ~ /TRIGGER/ ? "yes" : "no"), F}' "$f"
  done
 679 chars  TRIGGER=yes skills/ab-equivalence/SKILL.md
 292 chars  TRIGGER=yes skills/assess-findings/SKILL.md
 273 chars  TRIGGER=yes skills/assess-pr/SKILL.md
 502 chars  TRIGGER=yes skills/assess/SKILL.md
 650 chars  TRIGGER=yes skills/deslop/SKILL.md
 703 chars  TRIGGER=yes skills/ghreport/SKILL.md
 841 chars  TRIGGER=yes skills/ghsync/SKILL.md
 404 chars  TRIGGER=yes skills/huddle/SKILL.md
 696 chars  TRIGGER=yes skills/marathon/SKILL.md
 490 chars  TRIGGER=yes skills/pr-review-merge/SKILL.md
 858 chars  TRIGGER=yes skills/semantic-compress/SKILL.md
 627 chars  TRIGGER=yes skills/skill-forge/SKILL.md
  28 chars  TRIGGER=no  commands/6hats.md
  65 chars  TRIGGER=no  commands/fix-develop.md
  85 chars  TRIGGER=no  commands/fix-pr.md
  95 chars  TRIGGER=no  commands/issues.md
 101 chars  TRIGGER=no  commands/tm-marathon-config-example.md
  44 chars  TRIGGER=no  commands/tm.md
  82 chars  TRIGGER=no  commands/understand.md
```

The longest description is `semantic-compress` at 858 characters, 166 under the 1,024 ceiling. Every skill carries a TRIGGER clause; no command does, which is consistent with `disable-model-invocation: true`.

### Menu entries and argument consumption

```
$ rg -l '^name:' skills/*/SKILL.md commands/*.md | wc -l
19
$ rg -n '\$ARGUMENTS' skills/*/SKILL.md
skills/assess/SKILL.md:57:**$ARGUMENTS**
```

Nineteen components claim a `/` menu entry today. Exactly one skill consumes `$ARGUMENTS`: `assess`, at line 57, inside a `chat-skip` region so the standalone ZIP does not carry it. No skill declares the matching `argument-hint`.

### Hand-rolled `SKILL_DIR` sites

Six bootstrap sites, each a two-line pair: a `CLAUDE_PLUGIN_ROOT` branch and a `~/.claude/skills` fallback.

```
$ rg -n --no-heading 'SKILL_DIR="\$\{CLAUDE_PLUGIN_ROOT' skills/ | sort
skills/assess-pr/SKILL.md:271:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/assess/SKILL.md:290:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/assess/SKILL.md:364:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/assess/SKILL.md:459:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/ghreport/SKILL.md:41:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/ghreport}"
skills/ghsync/SKILL.md:32:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/ghsync}"

$ rg -n --no-heading 'realpath ~/\.claude/skills' skills/ | sort
skills/assess-pr/SKILL.md:272:SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
skills/assess/SKILL.md:291:SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
skills/assess/SKILL.md:365:SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
skills/assess/SKILL.md:460:SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
skills/ghreport/SKILL.md:42:SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/ghreport/SKILL.md)")}"
skills/ghsync/SKILL.md:33:SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/ghsync/SKILL.md)")}"
```

The line numbers in the programme PRD's Technical Context row hold at 44bddbf: `skills/assess/SKILL.md:290,364,459`, `assess-pr/SKILL.md:271`, `ghsync/SKILL.md:32`, `ghreport/SKILL.md:41`.

Consumer lines that read the variable:

```
$ rg -c --no-heading '\$SKILL_DIR/scripts/' skills/ | sort
skills/assess-pr/SKILL.md:1
skills/assess/references/monorepo-scoping.md:2
skills/assess/scripts/lib/interactivity.py:1
skills/assess/SKILL.md:7
skills/ghreport/SKILL.md:2
skills/ghsync/SKILL.md:2
```

`skills/assess/scripts/lib/interactivity.py:14` is a docstring mention, not a path expansion. Every `$SKILL_DIR` line in an assess file sits on a `chat-replace` target line or inside a `chat-skip` region, which is what keeps `SKILL_DIR` out of the standalone ZIP:

```
$ rg -n --sort path -B1 '\$SKILL_DIR/scripts/' skills/assess/SKILL.md skills/assess-pr/SKILL.md | grep -E 'chat-replace|SKILL_DIR/scripts'
skills/assess/SKILL.md-236-   <!-- chat-replace:treemap-exclude-example -->
skills/assess/SKILL.md:237:   uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" --exclude regulatory-raw --exclude vetted-context --exclude '*.csv'
skills/assess/SKILL.md-296-<!-- chat-replace:uv-treemap -->
skills/assess/SKILL.md:297:uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json"
skills/assess/SKILL.md-300-<!-- chat-replace:uv-doc-graph -->
skills/assess/SKILL.md:301:uv run "$SKILL_DIR/scripts/doc-graph-svg.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/doc-graph.svg"
skills/assess/SKILL.md-307-<!-- chat-replace:uv-core -->
skills/assess/SKILL.md:308:uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT"
skills/assess/SKILL.md-368-<!-- chat-replace:uv-core-mutation -->
skills/assess/SKILL.md:369:uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT" --opt-in-mutation
skills/assess/SKILL.md-373-<!-- chat-replace:uv-treemap-overlay -->
skills/assess/SKILL.md:374:uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json" --test-pressure "$REPO_ROOT/.assess/run-context.json"
skills/assess/SKILL.md-462-<!-- chat-replace:uv-finalize -->
skills/assess/SKILL.md:463:uv run "$SKILL_DIR/scripts/assess_finalize.py" "$REPO_ROOT"
skills/assess-pr/SKILL.md-274-<!-- chat-replace:uv-emit-workflow -->
skills/assess-pr/SKILL.md:275:uv run "$SKILL_DIR/scripts/assess_emit_workflow.py" "$REPO_ROOT"
```

One further `CLAUDE_PLUGIN_ROOT` use is not a script path and stays: `skills/assess/references/consent-lifecycle.md:15` reads `${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/.claude-plugin/plugin.json}` to learn the plugin version. A skill-directory substitution cannot reach a sibling manifest.

### `ghreport.sh` sibling lookup

```
$ rg -n --no-heading 'GHSYNC_SH' skills/ghreport/scripts/ghreport.sh
283:GHSYNC_SH="$SCRIPT_DIR/../../ghsync/scripts/ghsync.sh"
284:if [ ! -f "$GHSYNC_SH" ]; then
285:    GHSYNC_SH="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills}/ghsync/scripts/ghsync.sh"
287:if [ ! -f "$GHSYNC_SH" ]; then
297:disc_out=$(bash "$GHSYNC_SH" --porcelain --org "$ORG" --root "$ROOT") || {
```

The block runs from line 282 (`SCRIPT_DIR=`) to line 290. The env fallback at line 285 is dead under a plugin install: it composes `$CLAUDE_PLUGIN_ROOT/ghsync/scripts/ghsync.sh`, while a plugin's skills live at `<plugin root>/skills/<name>/`. Only the `$HOME/.claude/skills` half of that expression has ever resolved. The sibling lookup at line 283 is what works today, and it keeps working inside `plugins/gh-org/skills/` after PR1 because both skills stay siblings.

### Agent frontmatter

```
$ for f in agents/*.md; do printf '%-32s' "$f"; awk '/^---$/{c++; if(c==2) exit; next} c==1 && /^[a-zA-Z-]+:/{k=$0; sub(/:.*/,"",k); keys=keys k " "; if(k=="model") m=$2; if(k=="color") col=$2} END{printf "keys=[%s] model=%s color=%s", keys, m, col}' "$f"; echo; done
agents/assess-layer-scorer.md   keys=[name description model color ] model=inherit color=cyan
agents/black-hat.md             keys=[name description model color ] model=inherit color=red
agents/blue-hat.md              keys=[name description model color ] model=inherit color=blue
agents/green-hat.md             keys=[name description model color ] model=inherit color=green
agents/README.md                keys=[] model= color=
agents/red-hat.md               keys=[name description model color ] model=inherit color=red
agents/scribe.md                keys=[name description model color ] model=inherit color=indigo
agents/white-hat.md             keys=[name description model color ] model=inherit color=cyan
agents/yellow-hat.md            keys=[name description model color ] model=inherit color=yellow
```

Nine `.md` files sit under `agents/`; eight carry frontmatter. The ninth, `agents/README.md`, has none and is the non-component that `claude plugin details` reports as an agent named `README`. R3 (WS1) deletes it, so WS3 edits eight files, not nine.

No agent declares a tool field, `hooks`, `mcpServers`, or `permissionMode`. `CLAUDE.md:52-59` states the required agent frontmatter as `name`, `description`, `model`, `color` and names no tool field.

### What the gates enforce today

```
$ GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest pytest tests/test_plugin_contract.py -q | tail -1
355 passed in 0.12s
```

The count is 355 on `main` at 44bddbf; the branch carrying this spec reports 359, because four doc-level tests parametrize over every authored markdown file and pick this one up.

`tests/test_plugin_contract.py` asserts `name` matches the directory and `description` is non-empty (`test_skill_frontmatter`) and that the frontmatter carries `TRIGGER` (`test_skill_has_trigger_clause`). Nothing asserts a tool field, an `argument-hint`, or a script-path form. `scripts/tests/test_integration.py::TestAssessBuild::test_no_skill_dir_reference` asserts the literal `SKILL_DIR` appears in no `.md` inside the assess ZIP; the same class asserts `CLAUDE_PLUGIN_ROOT` is absent. Both run under the required `scripts/ pytest` job.

## Design

### `${CLAUDE_SKILL_DIR}` replaces the bootstrap (R4)

Each of the six bootstrap pairs is deleted along with its explanatory comment, and each consumer line becomes the literal `${CLAUDE_SKILL_DIR}/scripts/<x>`. Claude Code text-substitutes that token in skill markdown, and it resolves to the skill's own directory under a family install and under the meta-plugin, where the cache copy dereferences the symlink (D5).

Rejected: keeping `CLAUDE_PLUGIN_ROOT`. It names a plugin-relative path (`.../skills/assess`) that the split changes for every skill, and under the meta-plugin it points at the umbrella rather than the owning family, so the path is right only while the symlink layout holds. Rejected: keeping the `~/.claude/skills` fallback. It exists because a stray hand-placed personal copy masked a clean-install break (#32) and was then copied five times; it makes a broken install look healthy, which is the failure it was meant to catch.

The `SKILL_DIR` literal must not survive into a standalone ZIP, and `${CLAUDE_SKILL_DIR}` contains that substring. The rewrite therefore keeps every `chat-skip` region and every `chat-replace` marker paired with exactly one following line, so the transform in `scripts/transform_skill.py` still strips or replaces each site. `scripts/tests/test_integration.py` is the check, not a review reading.

### `ghreport.sh` takes the `ghsync` path as an argument (R4)

Text substitution reaches skill markdown, not a running script, so `ghreport.sh` cannot read `${CLAUDE_SKILL_DIR}`. The script gains a `--ghsync <path>` flag parsed beside the existing `--org` and `--root` cases at `skills/ghreport/scripts/ghreport.sh:40-42`; `ghreport/SKILL.md` passes `${CLAUDE_SKILL_DIR}/../ghsync/scripts/ghsync.sh`. The sibling lookup at line 283 stays as the default when the flag is absent, so a direct `bash ghreport.sh` from a checkout keeps working. The dead env fallback at line 285 is deleted; the not-found error at line 288 loses its `~/.claude/skills` clause and names the flag instead.

### Frontmatter fields (R4)

- `user-invocable: false` on the five library skills: `assess-findings`, `assess-pr`, `ab-equivalence`, `marathon`, `pr-review-merge`. Nineteen menu entries drop to thirteen once WS1 removes the config example. Descriptions stay always-on; this is a menu fix, not a token fix.
- `argument-hint` where the body consumes an argument. The measurement gives exactly one such skill, `assess`, which reads `$ARGUMENTS` at line 57. `huddle` also gains one, `[solo|2|3|5|8] <topic>`, but WS2 adds it inside PR1 (R12); WS3 asserts it rather than adding it. `ghsync`, `ghreport`, `deslop`, `semantic-compress`, and `skill-forge` take their input through flags on a bash invocation or from conversation, consume no `$ARGUMENTS`, and get no hint.
- `allowed-tools`: WS3 adds none. Only the matching form `Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/* *)` matches a `uv run` invocation; the bare `Bash(${CLAUDE_SKILL_DIR}/scripts/*)` form never does. No skill's tool surface is its own scripts alone - `assess`, `ghsync`, and `ghreport` all shell out to `git`, `gh`, and `rg` as well - so a matching-form allowlist would block the rest of the skill. A contract test records the rule instead, so a future author cannot land the bare form.
- Descriptions are untouched. Every one keeps its TRIGGER clause and stays under 1,024 characters.

### `disallowedTools` on every agent (R5)

Each of the eight agent files gains `disallowedTools: Write, Edit, NotebookEdit`. No agent body instructs a file write: `scribe` composes prose and returns it to the chair, and `assess-layer-scorer` returns a structured verdict that the `assess-findings` step renders (`agents/assess-layer-scorer.md:12`).

A `tools:` allowlist is rejected. It replaces the whole tool set, so it would strip `SendMessage` and `ToolSearch` from the hat agents, and huddle team mode is built on both: `skills/huddle/SKILL.md:40` gates the mode on `SendMessage` plus background teammates, line 58 requires a `ToolSearch("select:SendMessage")` probe, and line 199 has each member share findings by one `SendMessage` per peer. Losing them degrades huddle silently rather than loudly. "Read-only Bash" has no frontmatter syntax; the documented example uses a `PreToolUse` hook, which plugin agents ignore. `hooks`, `mcpServers`, and `permissionMode` stay absent for the same reason.

`CLAUDE.md`'s agent conventions gain `disallowedTools` as a required field, one line under the existing `color` bullet.

## Requirements

1. No `SKILL.md` resolves a script path through `CLAUDE_PLUGIN_ROOT`. Verify: `rg -n 'SKILL_DIR="\$\{CLAUDE_PLUGIN_ROOT' plugins/` returns nothing.
2. No skill or script resolves a path through `~/.claude/skills`. Verify: `rg -n 'realpath ~/\.claude/skills' plugins/` and `rg -n 'CLAUDE_PLUGIN_ROOT:-\$HOME/\.claude/skills' plugins/` both return nothing. `skills/skill-forge/SKILL.md:172` mentions a home-directory skill path as prose about where a promoted file may live; that is content, not resolution, and stays.
3. Every script invocation in skill markdown - `SKILL.md` and `references/` alike - uses the literal `${CLAUDE_SKILL_DIR}/scripts/<x>`. Verify: `rg -c 'CLAUDE_SKILL_DIR/scripts/' plugins/` accounts for the fourteen path-expanding lines counted above; the fifteenth match in that count is the docstring in `interactivity.py`, which is prose.
4. `skills/assess/references/consent-lifecycle.md`'s plugin-manifest read is unchanged, and is the only remaining `CLAUDE_PLUGIN_ROOT` occurrence under `plugins/`.
5. `ghreport.sh` accepts `--ghsync <path>`, defaults to the sibling lookup when the flag is absent, and exits non-zero with a message naming the flag when neither resolves. Verify: `tests/test_ghreport.py::test_missing_ghsync_exits_nonzero` plus a new `test_explicit_ghsync_path` case.
6. `assess-findings`, `assess-pr`, `ab-equivalence`, `marathon`, and `pr-review-merge` declare `user-invocable: false`; no other skill does. Verify: `rg -l '^user-invocable: false' plugins/ | wc -l` prints 5.
7. `assess` declares `argument-hint`; `huddle`'s `argument-hint` from WS2 is present and unmodified. Verify: `rg -n '^argument-hint:' plugins/assess plugins/huddle`.
8. Every skill description keeps its TRIGGER clause and stays under 1,024 characters. Verify: `test_skill_has_trigger_clause` plus a new description-length ceiling test.
9. Any `allowed-tools` value present matches the documented `Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/* *)` form. Verify: a new `test_allowed_tools_form` contract test, green over a tree that declares none.
10. All eight agent files declare `disallowedTools: Write, Edit, NotebookEdit`, and none declares `tools`, `hooks`, `mcpServers`, or `permissionMode`. Verify: a new `test_agent_disallowed_tools` contract test parametrized over every agent file.
11. `CLAUDE.md` lists `disallowedTools` as required agent frontmatter.
12. A clean-profile run is pasted into the assess PR: in a profile with no `~/.claude/skills/assess`, `claude --plugin-dir plugins/assess` invoking `/assess` finds its scripts and completes Step 1. The paste carries the command, the profile path, and the resolved script path.
13. Every standalone ZIP builds with no `SKILL_DIR` or `CLAUDE_PLUGIN_ROOT` in any bundled `.md`. Verify: the `scripts/ pytest` job.
14. Each WS3 PR bumps its plugin's `version` and adds a `CHANGELOG.md` entry, per R10 and R15.

## Verification

- `tests/test_plugin_contract.py`: existing `test_skill_frontmatter` and `test_skill_has_trigger_clause`; new `test_agent_disallowed_tools`, `test_allowed_tools_form`, and the description-length ceiling. Runs under the required `plugin contract pytest` job.
- `scripts/tests/test_integration.py::TestAssessBuild::test_no_skill_dir_reference` and `::test_no_plugin_root_reference`, plus the equivalent cases on the `huddle`, `skill-forge`, `deslop`, and `semantic-compress` ZIPs. These are what catch a `${CLAUDE_SKILL_DIR}` literal escaping a `chat-skip` region.
- `tests/test_ghreport.py::test_missing_ghsync_exits_nonzero` (updated for the deleted env fallback), `::test_unknown_flag_exits_nonzero`, `::test_help_exits_zero`, and the new `test_explicit_ghsync_path`.
- The clean-profile paste of requirement 12, recorded in the assess PR body. It is the only check that proves substitution reaches a live run; no unit test can.
- A live `/ghreport` run against one org, pasted into the gh-org PR, showing discovery delegated through the passed `ghsync` path.
- No `ab-equivalence` gate. The rewrite changes a path expression, not instructions, and the PRD records that no runner-consumable transfer set exists for `assess`. The freeze here is the clean-profile run plus the ZIP leak tests. `huddle`'s transfer set is a WS4 prerequisite (D9) and is not a WS3 dependency, because WS3 touches no huddle body text.

## Breadcrumbs

WS3 leaves no shim and adds no `remove-in` marker. Nothing it removes was ever a documented interface: the `~/.claude/skills/<name>` fallback is an undocumented hand-placement path, and the `CLAUDE_PLUGIN_ROOT` bootstrap is internal to the skill body.

Two rows land in `docs/migration-2.0.md` (skeleton created by WS1, R18), both added by the repo-level PR so no two WS3 PRs edit that file:

| Change | Breadcrumb | Removed in |
|--------|-----------|------------|
| Five library skills leave the `/` menu (`user-invocable: false`) | Each stays model-invocable and keeps its description; the commands that call it are unchanged | never |
| Hand-placed `~/.claude/skills/<name>` script resolution dropped | Install the family plugin; `${CLAUDE_SKILL_DIR}` resolves under both a family install and the umbrella | never |

## Rollback

Each WS3 PR touches one plugin, so a revert is per plugin and independent. `git revert` of the squash commit restores that plugin's frontmatter, its bootstrap blocks, its `plugin.json` version, and its `CHANGELOG.md` entry in one commit; the revert must carry the version and changelog lines, or the same-PR bump test (R10) fails on the revert itself.

A failure found after merge is more likely a runtime path miss than a red check, since the checks pass on the substitution's text form. The narrow fix is a forward PR restoring the bootstrap pair in the single affected skill, not a revert of the plugin PR: the frontmatter fields in the same PR are independent of the path rewrite and carry no runtime risk.

The repo-level PR that adds the contract tests reverts on its own. Reverting it leaves the shipped `disallowedTools` and `user-invocable` fields in place, unasserted, which is the pre-WS3 state of enforcement and is green.

## Sequencing

Every WS3 PR requires PR1 (WS1 + WS2) merged, because each one edits files at their post-move paths. The five plugin PRs are parallel; the repo-level PR lands last, because its tests assert what the five have shipped.

| PR | Scope | May not touch |
|----|-------|---------------|
| WS3-1 `assess` | Four `SKILL_DIR` sites (three in `assess`, one in `assess-pr`), `user-invocable: false` on `assess-findings` and `assess-pr`, `argument-hint` on `assess`, `disallowedTools` on `assess-layer-scorer`, version bump, changelog | Any other plugin, `action.yml`, CI job names, `docs/migration-2.0.md`, `tests/test_plugin_contract.py` |
| WS3-2 `gh-org` | `ghsync` and `ghreport` `SKILL_DIR` sites, `ghreport.sh` `--ghsync` flag and error text, `tests/test_ghreport.py` cases, version bump, changelog | Any other plugin, `tests/test_ghsync_porcelain.py` behaviour, `docs/migration-2.0.md` |
| WS3-3 `skill-craft` | `user-invocable: false` on `ab-equivalence`, version bump, changelog | Any other plugin, any skill body, `docs/migration-2.0.md` |
| WS3-4 `delivery` | `user-invocable: false` on `marathon` and `pr-review-merge`, version bump, changelog | Any other plugin, the Marathon Configuration heading or its four runtime readers, `docs/migration-2.0.md` |
| WS3-5 `huddle` | `disallowedTools` on the six hat agents and `scribe`, version bump, changelog | `huddle/SKILL.md` body and its `argument-hint` (WS2 owns both), any other plugin, `docs/migration-2.0.md` |
| WS3-6 repo-level | `test_agent_disallowed_tools`, `test_allowed_tools_form`, the description-length ceiling, the `CLAUDE.md` agent-conventions line, the two `docs/migration-2.0.md` rows | Any `plugins/` component file, any plugin version, floor files, CI job names |

Two ordering constraints beyond the table. First, WS3-6 needs WS3-1 through WS3-5 merged, or `test_agent_disallowed_tools` fails on the unedited agents. Second, `CLAUDE.md` is also rewritten by WS5 (R7), which keeps the File conventions section: whichever of the two merges second rebases onto the other, and the `disallowedTools` bullet must be present in the merged result. No WS3 PR edits `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, `FLOOR.md`, `action.yml`, or any CI job `name:` (D7).
