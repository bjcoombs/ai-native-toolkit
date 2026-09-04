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

```
$ rg --no-heading -o '\$SKILL_DIR/scripts/' skills/ | wc -l
      15
```

Fourteen of the fifteen are path expansions. The fifteenth is `skills/assess/scripts/lib/interactivity.py:14`, a docstring inside a `lib/` module that quotes `uv run "$SKILL_DIR/scripts/assess_core.py"` as the launch line, to explain why the core has no controlling terminal. It is prose, not a path expansion. Every `$SKILL_DIR` line in an assess markdown file - `SKILL.md` and `references/` alike - sits on a `chat-replace` target line or inside a `chat-skip` region, which is what keeps `SKILL_DIR` out of the standalone ZIP. The audit below classifies all ten markdown occurrences; `interactivity.py` is not markdown, so no marker reaches it and the ZIP test asserts `.md` files only:

```
$ awk 'FNR==1{s=0;r=0} /chat-skip:start/{s=1} /chat-skip:end/{s=0} /chat-replace:/{r=FNR} /\$SKILL_DIR\/scripts\//{printf "%s:%d %s\n", FILENAME, FNR, (s ? "inside chat-skip" : (r==FNR-1 ? "chat-replace target" : "UNMARKED"))}' \
    skills/assess/SKILL.md skills/assess-pr/SKILL.md skills/assess/references/*.md
skills/assess/SKILL.md:237 chat-replace target
skills/assess/SKILL.md:297 chat-replace target
skills/assess/SKILL.md:301 chat-replace target
skills/assess/SKILL.md:308 chat-replace target
skills/assess/SKILL.md:369 chat-replace target
skills/assess/SKILL.md:374 chat-replace target
skills/assess/SKILL.md:463 chat-replace target
skills/assess-pr/SKILL.md:275 chat-replace target
skills/assess/references/monorepo-scoping.md:41 inside chat-skip
skills/assess/references/monorepo-scoping.md:49 inside chat-skip
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

### Platform features relied on

The Design section introduces three platform features the repo does not use anywhere today. Each comes from the Claude Code documentation, not from a precedent in the tree, so each carries its source here on the same terms `intent.md` D5 cites `plugins-reference` for the symlink meta-plugin.

| Feature | Where the Design uses it | Source |
|---------|--------------------------|--------|
| `${CLAUDE_SKILL_DIR}` text substitution in skill markdown | Replaces the six bootstrap pairs (R4) | code.claude.com `skills`, section "Available string substitutions" (under "Frontmatter reference") |
| `user-invocable: false` skill frontmatter | Five library skills leave the `/` menu (R4) | code.claude.com `skills`, section "Control who invokes a skill" |
| `disallowedTools` agent frontmatter | Eight agents deny `Write`, `Edit`, `NotebookEdit` (R5) | code.claude.com `sub-agents`, section "Supported frontmatter fields" |

`user-invocable` is documented on the `skills` page, not on `plugins-reference`; `plugins-reference` carries the symlink section D5 cites, not this key. Those pages, plus `plugins-reference` and `plugins`, are named on the programme PRD's "Reference docs" line as the sources the programme's decisions were taken from.

Excluding this spec file, none of the three names occurs anywhere in the repo:

```
$ rg -n 'CLAUDE_SKILL_DIR|disallowedTools|user-invocable' docs/ skills/ commands/ agents/ scripts/ tests/ CLAUDE.md -g '!docs/design/2026-09-modernization/frontmatter/spec.md'
$ echo $?
1
```

So no text check can prove a token or key name is right. Requirements 1 through 11 assert the content of our own files and would all stay green on a misspelled token. The attestations are the live runs: requirement 12 for `${CLAUDE_SKILL_DIR}`, and the `claude plugin details` observations in requirements 6 and 10 for the two frontmatter keys.

## Design

### `${CLAUDE_SKILL_DIR}` replaces the bootstrap (R4)

Each of the six bootstrap pairs is deleted along with its explanatory comment, and each consumer line becomes the literal `${CLAUDE_SKILL_DIR}/scripts/<x>`. Claude Code text-substitutes that token in skill markdown (code.claude.com `skills`), and it resolves to the skill's own directory under a family install. Under the meta-plugin it is expected to resolve to the dereferenced target rather than the umbrella's symlink, because the cache copy dereferences. Probe 1, recorded in [`../probes.md`](../probes.md) by the probes PR (#295), reports that a throwaway meta-plugin whose skill and agent are symlinks into a sibling family plugin, installed from a GitHub-sourced marketplace, lists each of them once under `claude plugin details ant-umbrella@ant-probe` (`Skills (1) probeskill`, `Agents (1) probe-agent`); that record is what moves D5 to Decided. The probes PR has landed on `main` and this branch predates it, so the copy of `../probes.md` alongside this file is still the placeholder until WS3 merges - WS3 therefore cites the record rather than asserting the result. `--plugin-dir` cannot exercise that path (external symlinks are skipped for local installs, D5), so requirement 12 re-verifies the resolution from a GitHub-sourced marketplace.

The one non-path mention of the old variable goes with the six pairs. `skills/assess/scripts/lib/interactivity.py:14` quotes `uv run "$SKILL_DIR/scripts/assess_core.py"` as the launch line that explains why the core has no controlling terminal; WS3-1 rewrites that quote to `${CLAUDE_SKILL_DIR}`. Left alone it would name a variable no assess file sets - a lying map of the launch path inside a `lib/` module.

Rejected: keeping `CLAUDE_PLUGIN_ROOT`. It names a plugin-relative path (`.../skills/assess`) that the split changes for every skill, and under the meta-plugin it points at the umbrella rather than the owning family, so the path is right only while the symlink layout holds. Rejected: keeping the `~/.claude/skills` fallback. It exists because a stray hand-placed personal copy masked a clean-install break (#32) and was then copied five times; it makes a broken install look healthy, which is the failure it was meant to catch.

The `SKILL_DIR` literal must not survive into a standalone ZIP, and `${CLAUDE_SKILL_DIR}` contains that substring. The rewrite therefore keeps every `chat-skip` region and every `chat-replace` marker paired with exactly one following line, so the transform in `scripts/transform_skill.py` still strips or replaces each site. `scripts/tests/test_integration.py` is the check, not a review reading.

### `ghreport.sh` takes the `ghsync` path as an argument (R4)

Text substitution reaches skill markdown, not a running script, so `ghreport.sh` cannot read `${CLAUDE_SKILL_DIR}`. The script gains a `--ghsync <path>` flag parsed beside the existing `--org` and `--root` cases at `skills/ghreport/scripts/ghreport.sh:40-42`; `ghreport/SKILL.md` passes `${CLAUDE_SKILL_DIR}/../ghsync/scripts/ghsync.sh`. The sibling lookup at line 283 stays as the default when the flag is absent, so a direct `bash ghreport.sh` from a checkout keeps working. The dead env fallback at line 285 is deleted. At line 288 the whole parenthetical is replaced, not trimmed: today the line reads `Error: ghsync.sh not found (looked next to this script and under CLAUDE_PLUGIN_ROOT / ~/.claude/skills).`, and dropping only the `~/.claude/skills` clause would leave `CLAUDE_PLUGIN_ROOT` in the string and break requirement 4. The replacement names the sibling path that was tried and the `--ghsync` argument, so no `CLAUDE_PLUGIN_ROOT` or `~/.claude/skills` text survives anywhere in the script.

### Frontmatter fields (R4)

- `user-invocable: false` on the five library skills: `assess-findings`, `assess-pr`, `ab-equivalence`, `marathon`, `pr-review-merge`. WS1's removal of the config example takes nineteen menu entries to eighteen, and these five take it to thirteen. Descriptions stay always-on; this is a menu fix, not a token fix.
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
3. Every script invocation in skill markdown - `SKILL.md` and `references/` alike - uses the literal `${CLAUDE_SKILL_DIR}/scripts/<x>`, and the `interactivity.py:14` docstring is rewritten with it so no reference to the old variable survives. Verify: `rg --no-heading -o 'CLAUDE_SKILL_DIR/scripts/' plugins/ | wc -l` prints 15, matching the pre-change count above - the fourteen path-expanding lines plus that one docstring. Both halves must move: rewriting only the fourteen prints 14 and leaves a docstring naming a variable nothing sets.
4. `skills/assess/references/consent-lifecycle.md`'s plugin-manifest read is unchanged, and is the only remaining `CLAUDE_PLUGIN_ROOT` occurrence under `plugins/`.
5. `ghreport.sh` accepts `--ghsync <path>`, defaults to the sibling lookup when the flag is absent, and exits non-zero with a message naming the flag when neither resolves. Verify: `tests/test_ghreport.py::test_missing_ghsync_exits_nonzero` plus a new `test_explicit_ghsync_path` case.
6. `assess-findings`, `assess-pr`, `ab-equivalence`, `marathon`, and `pr-review-merge` declare `user-invocable: false`; no other skill does, and Claude Code drops the five from the `/` menu. Verify, in two parts: `rg -l '^user-invocable: false' plugins/ | wc -l` prints 5, which asserts our own text; and a before/after `claude plugin details` paste per affected plugin (`assess`, `skill-craft`, `delivery`), which asserts the platform acts on the key. Current state counts nineteen menu entries; WS1's removal of the config example takes that to eighteen, and these five take it to thirteen, so the after paste shows thirteen across the six families. Only the paste fails if the key name is wrong.
7. `assess` declares `argument-hint`; `huddle`'s `argument-hint` from WS2 is present and unmodified. Verify: `rg -n '^argument-hint:' plugins/assess plugins/huddle`.
8. Every skill description keeps its TRIGGER clause and stays under 1,024 characters. Verify: `test_skill_has_trigger_clause` plus a new description-length ceiling test.
9. Any `allowed-tools` value present matches the documented `Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/* *)` form. Verify: a new `test_allowed_tools_form` contract test carrying two cases. The tree scan is vacuous while no skill declares the key, so it cannot show the checker works; the second case is a negative unit test that feeds the bare `Bash(${CLAUDE_SKILL_DIR}/scripts/*)` form directly to the checker and asserts it is rejected. Without that case the guardrail can be broken from birth and stay green until the first author needs it.
10. All eight agent files declare `disallowedTools: Write, Edit, NotebookEdit`, and none declares `tools`, `hooks`, `mcpServers`, or `permissionMode`, and Claude Code reads the restriction. Verify, in two parts: a new `test_agent_disallowed_tools` contract test parametrized over every agent file, which asserts our own text; and the agents section of a `claude plugin details` paste for `assess` (one agent) and `huddle` (seven), showing the tool restriction on each. `intent.md`'s Success Criteria already require a `claude plugin details` paste per family, so this reads the agents block of an output the programme takes anyway. If the key name is wrong the test still passes and the paste does not.
11. `CLAUDE.md` lists `disallowedTools` as required agent frontmatter.
12. Two live runs are pasted into the assess PR, because the substitution's text form is all any check can reach.
    - Family install: in a profile with no `~/.claude/skills/assess`, `claude --plugin-dir plugins/assess` invoking `/assess` finds its scripts and completes Step 1. The paste carries the command, the profile path, and the resolved script path.
    - Meta-plugin: the same skill installed from a GitHub-sourced marketplace, with a `claude plugin details ai-native-toolkit` paste showing `/assess` resolving and the resolved script path under the dereferenced skill directory, not the umbrella's symlink. `--plugin-dir` skips external symlinks for local installs (D5), so this leg cannot be dogfooded locally; Probe 1, recorded in [`../probes.md`](../probes.md) by the probes PR (#295), is the precedent: it reports a throwaway meta-plugin installed the same way, listing each symlinked skill and agent once.

    The second leg is the one that covers existing users: every `ai-native-toolkit@ai-native-toolkit` install runs through the umbrella after `/plugin update`, so a token that resolved only under a family install would break `/assess` for all of them while requirements 1 through 11 stayed green.
13. Every standalone ZIP builds with no `SKILL_DIR` or `CLAUDE_PLUGIN_ROOT` in any bundled `.md`. Verify: the `scripts/ pytest` job.
14. Each WS3 PR bumps its plugin's `version` and adds a `CHANGELOG.md` entry, per R10 and R15.

## Verification

- `tests/test_plugin_contract.py`: existing `test_skill_frontmatter` and `test_skill_has_trigger_clause`; new `test_agent_disallowed_tools`, `test_allowed_tools_form` (tree scan plus the negative unit case of requirement 9), and the description-length ceiling. Runs under the required `plugin contract pytest` job.
- `scripts/tests/test_integration.py::TestAssessBuild::test_no_skill_dir_reference` and `::test_no_plugin_root_reference`, plus the equivalent cases on the `huddle`, `skill-forge`, `deslop`, and `semantic-compress` ZIPs. These are what catch a `${CLAUDE_SKILL_DIR}` literal escaping a `chat-skip` region.
- `tests/test_ghreport.py::test_missing_ghsync_exits_nonzero` (updated for the deleted env fallback), `::test_unknown_flag_exits_nonzero`, `::test_help_exits_zero`, and the new `test_explicit_ghsync_path`.
- Both live runs of requirement 12 - the clean-profile family install and the GitHub-sourced marketplace meta-plugin install - recorded in the assess PR body. They are the only checks that prove substitution reaches a running skill; no unit test can.
- The `claude plugin details` pastes of requirements 6 and 10: thirteen menu entries after the five library skills are marked, and the tool restriction on the eight agents. With requirement 12 these are the three attestations for the platform features listed in Current state; every other check reads our own files.
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
| WS3-1 `assess` | Four `SKILL_DIR` sites (three in `assess`, one in `assess-pr`), the `interactivity.py:14` docstring quote, `user-invocable: false` on `assess-findings` and `assess-pr`, `argument-hint` on `assess`, `disallowedTools` on `assess-layer-scorer`, version bump, changelog | Any other plugin, `action.yml`, CI job names, `docs/migration-2.0.md`, `tests/test_plugin_contract.py` |
| WS3-2 `gh-org` | `ghsync` and `ghreport` `SKILL_DIR` sites, `ghreport.sh` `--ghsync` flag and error text, `tests/test_ghreport.py` cases, version bump, changelog | Any other plugin, `tests/test_ghsync_porcelain.py` behaviour, `docs/migration-2.0.md` |
| WS3-3 `skill-craft` | `user-invocable: false` on `ab-equivalence`, version bump, changelog | Any other plugin, any skill body, `docs/migration-2.0.md` |
| WS3-4 `delivery` | `user-invocable: false` on `marathon` and `pr-review-merge`, version bump, changelog | Any other plugin, the Marathon Configuration heading or its four runtime readers, `docs/migration-2.0.md` |
| WS3-5 `huddle` | `disallowedTools` on the six hat agents and `scribe`, version bump, changelog | `huddle/SKILL.md` body and its `argument-hint` (WS2 owns both), any other plugin, `docs/migration-2.0.md` |
| WS3-6 repo-level | `test_agent_disallowed_tools`, `test_allowed_tools_form`, the description-length ceiling, the `CLAUDE.md` agent-conventions line, the two `docs/migration-2.0.md` rows | Any `plugins/` component file, any plugin version, floor files, CI job names |

Two ordering constraints beyond the table. First, WS3-6 needs WS3-1 through WS3-5 merged, or `test_agent_disallowed_tools` fails on the unedited agents. Second, `CLAUDE.md` is also rewritten by WS5 (R7), which keeps the File conventions section: whichever of the two merges second rebases onto the other, and the `disallowedTools` bullet must be present in the merged result. No WS3 PR edits `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, `FLOOR.md`, `action.yml`, or any CI job `name:` (D7).
