# WS1 spec: Layout

## Intent

Satisfies briefs **R1** (multi-plugin marketplace layout), **R2** (commands become skills), **R3** (no non-components in component directories), **R11** (everything that references paths still resolves), **R18** (the `docs/migration-2.0.md` skeleton) and **R20** (user-facing migration text) of the programme recorded in [../intent.md](../intent.md) (index: [../README.md](../README.md)), parsed into Task Master tag `modernization-layout`. The R briefs live in the programme PRD, an un-versioned Task Master working document kept outside this repository, so each one is summarised inline at the point this spec relies on it. Decisions are cited by ID and not re-argued.

## Current state

Every number below was measured against this checkout at `0c6d3ad`, the commit `main` carried when the spec was written, with Claude Code 2.1.260 and the plugin installed at 1.56.0. Each block shows the command and its real output.

```
$ git rev-parse 0c6d3ad
0c6d3ade41556d8e4906787ac4fde97ff140410f

$ claude --version
2.1.260 (Claude Code)

$ rg -n '"version"' .claude-plugin/plugin.json
4:  "version": "1.56.0",
```

`main` has moved past that commit since the spec was written - it carried `22adf5d` when this section was last revised - and no measurement below is stale: every intervening commit touches only a spec under `docs/design/2026-09-modernization/` (this one included), the directory each path command below already excludes.

```
$ git log --oneline -1 22adf5d
22adf5d docs: Add WS1 layout spec for the modernization programme (#303)

$ git diff --name-only 0c6d3ad 22adf5d
docs/design/2026-09-modernization/evals/spec.md
docs/design/2026-09-modernization/frontmatter/spec.md
docs/design/2026-09-modernization/layout/spec.md
```

### Three component directories, 29 loaded components, three of them not components

```
$ ls commands/*.md | wc -l; ls -d skills/*/ | wc -l; ls agents/*.md | wc -l
       8
      12
       9

$ ls -d skills/*/
skills/ab-equivalence/
skills/assess-findings/
skills/assess-pr/
skills/assess/
skills/deslop/
skills/ghreport/
skills/ghsync/
skills/huddle/
skills/marathon/
skills/pr-review-merge/
skills/semantic-compress/
skills/skill-forge/
```

Twelve skill directories plus eight `commands/*.md` files is the 20 the runtime reports as skills; the nine `agents/*.md` files are the nine it reports as agents, and the inventory line below names all 29. The always-on cost and the per-component split, run for real against the installed plugin:

```
$ claude plugin details ai-native-toolkit
ai-native-toolkit 1.56.0
  Description: Skills for AI-native development. /assess scores a codebase's readiness for AI agent contributors (0-8 layered contract model) and generates a Codecov-style complexity hotspot SVG plus a colour-blind-safe doc-navigability graph. /huddle runs structured multi-perspective deliberation using Six Thinking Hats with Fibonacci team sizing. /deslop detects and removes the telltale signs of AI writing from prose. /ghsync bulk-clones and keeps in sync every GitHub repo you can access across an org or personal account, for onboarding into a new enterprise. /ghreport is its read-only companion: it reuses ghsync's repo discovery to query each repo's remote state (open PRs, CI on the default branch, security alerts, branch protection) and rolls it into a terminal summary plus a timestamped markdown report of what state an org's repos are in. /skill-forge hardens a skill through judge-panel refinement rounds until it clears a 3-tier promotion gate - a prove-and-promote quality gate that ran on itself. /semantic-compress optimizes an LLM-directed document while preserving what it does, with two transforms gated on the ab-equivalence A/B harness: compress (a local core->pointer pass and an A/B-validated distill loop that produces the smallest behaviourally-equivalent version of a whole document or skill) and directive-clarity (rewrites latent-action instructions - bare negations, facts-not-actions, vague pointers - into directives that name the action, validated by a measured directness gain at zero regression). Also bundles personal workflow commands: /tm (Task Master orchestration), /issues (GitHub-issue marathon - triage open issues then run agent-ready ones to merge with Agent Teams), /fix-pr and /fix-develop (autonomous PR/branch fix loops). /tm, /issues, /fix-pr, and /fix-develop share the marathon and pr-review-merge skills for team orchestration and PR review/merge.
  Source: ai-native-toolkit@ai-native-toolkit

Component inventory
  Skills (20)  6hats, README, ab-equivalence, assess, assess-findings, assess-pr, deslop, fix-develop, fix-pr, ghreport, ghsync, huddle, issues, marathon, pr-review-merge, semantic-compress, skill-forge, tm, tm-marathon-config-example, understand
  Agents (9)  black-hat, yellow-hat, red-hat, blue-hat, white-hat, README, green-hat, scribe, assess-layer-scorer
  Hooks (0)
  MCP servers (0)
  LSP servers (0)

Projected token cost
  Always-on:   ~3,287 tok   added to every session

Per-component (rounded)
  component                   always-on  on-invoke
  pr-review-merge                  ~190      ~4.7k
  skill-forge                      ~240     ~10.1k
  ghsync                           ~310      ~2.1k
  semantic-compress                ~320      ~8.6k
  assess-pr                        ~110       ~10k
  marathon                         ~260     ~16.6k
  huddle                           ~150     ~14.6k
  ghreport                         ~260      ~1.5k
  ab-equivalence                   ~260      ~4.6k
  assess                           ~190     ~18.4k
  deslop                           ~240      ~3.6k
  assess-findings                  ~120     ~20.6k
  black-hat                         ~50      ~1.3k
  yellow-hat                        ~50       ~900
  red-hat                           ~50      ~1.2k
  blue-hat                          ~40       ~900
  white-hat                         ~40       ~980
  README                           < 20       ~430
  green-hat                         ~40       ~930
  scribe                            ~30       ~680
  assess-layer-scorer               ~70     ~19.6k
  understand                        ~40       ~850
  fix-develop                       ~30        ~2k
  fix-pr                            ~40      ~1.1k
  README                           < 20       ~620
  issues                            ~40      ~2.3k
  6hats                            < 20       ~170
  tm-marathon-config-example        ~50       ~980
  tm                                ~20        ~5k

  On-invoke cost is paid each time a skill or agent fires.
  Token counts are estimates and may differ from actual usage.
Exit code: 0
```

Summing the per-component column into the six families of R1, plus the three components that are not components:

| Family | Components summed | Always-on |
|--------|-------------------|-----------|
| `assess` | assess 190, assess-findings 120, assess-pr 110, assess-layer-scorer 70 | ~490 |
| `huddle` | huddle 150, understand 40, 6hats <20, six hats 50+50+50+40+40+40, scribe 30 | ~510 |
| `deslop` | deslop 240 | ~240 |
| `skill-craft` | skill-forge 240, semantic-compress 320, ab-equivalence 260 | ~820 |
| `gh-org` | ghsync 310, ghreport 260 | ~570 |
| `delivery` | tm 20, issues 40, fix-pr 40, fix-develop 30, marathon 260, pr-review-merge 190 | ~580 |
| Not components | `README` skill <20, `README` agent <20, `tm-marathon-config-example` 50 | ~90 |

The two `README` rows are the leak R3 names: the skill comes from `commands/README.md` (a flat file in `commands/` loads as a skill), the agent from `agents/README.md` (every `.md` in `agents/` loads as an agent). `skills/README.md` is not in the inventory, because skills are discovered as `<dir>/SKILL.md`; it is still a non-component sitting on a path that ceases to exist.

The `assess` family's ~490 is the number R1's under-600 ceiling is set against, with no aliases still to add.

### The seven `commands/*.md` files are already skills in every respect but their format

```
$ rg -c '^name:|disable-model-invocation' commands/*.md | sort
commands/6hats.md:2
commands/fix-develop.md:2
commands/fix-pr.md:2
commands/issues.md:2
commands/tm-marathon-config-example.md:2
commands/tm.md:2
commands/understand.md:2

$ rg -l 'argument-hint' commands/*.md | sort
commands/6hats.md
commands/fix-develop.md
commands/fix-pr.md
commands/issues.md
commands/tm.md
commands/understand.md

$ sed -n '1,6p' commands/issues.md
---
name: issues
disable-model-invocation: true
description: GitHub-issue marathon - triage open issues, then run agent-ready ones to merge with Agent Teams
argument-hint: [scope-label] (optional - narrows which open issues are considered; default: all open issues)
---
```

Every one of the seven carries `name` and `disable-model-invocation: true`; six carry `argument-hint` (`tm-marathon-config-example.md` does not). `commands/README.md` carries no frontmatter at all, which is why it loads under a `README` name with a sub-20-token description.

### Manifests and the validator baseline

```
$ wc -c .claude-plugin/plugin.json
    2171 .claude-plugin/plugin.json

$ cat .claude-plugin/marketplace.json
{
  "name": "ai-native-toolkit",
  "owner": {
    "name": "Ben Coombs"
  },
  "plugins": [
    {
      "name": "ai-native-toolkit",
      "source": "./",
      "description": "Skills for AI-native development: /assess (codebase readiness scoring + complexity hotspot SVG) and /huddle (Six Thinking Hats deliberation with Fibonacci team sizing). Also bundles personal workflow commands."
    }
  ]
}

$ claude plugin validate .
Validating marketplace manifest: <repo>/.claude-plugin/marketplace.json

⚠ Found 1 warning:

  ❯ description: No marketplace description provided. Adding a description helps users understand what this marketplace offers

✔ Validation passed with warnings
Exit code: 0
```

The `--strict` flag R1 puts in CI exists and is documented as the CI mode:

```
$ claude plugin validate --help | sed -n '9,12p'
  --strict    Treat warnings as errors (exit 1). Use in CI to fail on
              unrecognized fields, missing metadata, and other issues that the
              runtime tolerates.
```

Under `--strict` the marketplace warning above is an error, so the marketplace `description` R1 requires is a precondition for the CI job, not a cosmetic addition.

### Every hard-coded path a consumer holds

146 live references to a family skill path exist outside the historical design docs and the assess fixtures:

```
$ rg -n --no-heading -g '!docs/design/**' -g '!docs/superpowers/**' -g '!.assess/**' -g '!*.svg' \
    -g '!skills/assess/tests/fixtures/**' \
    'skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b' . | wc -l
146

$ rg -n --no-heading -g '!docs/design/**' -g '!docs/superpowers/**' \
    -g '!skills/assess/tests/fixtures/**' \
    'skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b' . | wc -l
378
```

The second run is the same command without `-g '!.assess/**' -g '!*.svg'`: the 232 extra matches are the tracked `/assess` self-run outputs and the SVGs they embed, which is why Requirement 10's backstop keeps both exclusions.

The consumers that a required CI job runs, by file:

```
$ rg -n '"skills"|skills/' tests/*.py | sort
tests/test_ghreport.py:18:GHREPORT = REPO / "skills" / "ghreport" / "scripts" / "ghreport.sh"
tests/test_ghreport.py:239:    env["HOME"] = str(tmp_path / "fakehome")  # no ~/.claude/skills/ghsync there
tests/test_ghsync_porcelain.py:20:GHSYNC = REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh"
tests/test_plugin_contract.py:13:SKILLS = REPO / "skills"
tests/test_skill_forge_instruction_files.py:148:    runner = REPO / "skills" / "ab-equivalence" / "references" / "runner-prompt.md"
tests/test_skill_forge_instruction_files.py:22:FORGE = REPO / "skills" / "skill-forge"

$ rg -n "skills" scripts/tests/test_transform.py scripts/tests/test_floor_check.py | sort
scripts/tests/test_floor_check.py:126:    marathon = repo_root / "skills" / "marathon" / "SKILL.md"
scripts/tests/test_transform.py:160:    for md in Path("../skills/assess").rglob("*.md"):
scripts/tests/test_transform.py:211:    decomposition bundles two sub-skills with plugin-only script paths). Those
scripts/tests/test_transform.py:434:    source = Path("../skills/huddle/SKILL.md").read_text("utf-8")
scripts/tests/test_transform.py:469:    for md in Path("../skills/huddle").rglob("*.md"):

$ rg -n 'parents\[3\]' skills/assess/tests/*.py skills/assess/scripts/*.py | sort
skills/assess/scripts/assess_core.py:347:    plugin_json = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
skills/assess/scripts/complexity-treemap.py:548:    plugin_json = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
skills/assess/tests/test_action_contract.py:18:_ACTION_PATH = Path(__file__).resolve().parents[3] / "action.yml"
skills/assess/tests/test_assess_core.py:1671:    repo = Path(__file__).resolve().parents[3]  # has an ownership map (lib README)
skills/assess/tests/test_assess_core.py:1696:    repo = Path(__file__).resolve().parents[3]
skills/assess/tests/test_decomposition_parity.py:30:REPO_ROOT = Path(__file__).resolve().parents[3]
skills/assess/tests/test_ownership_parser.py:376:    repo_root = Path(__file__).resolve().parents[3]  # repo top
skills/assess/tests/test_structure_drift.py:254:    repo_root = Path(__file__).resolve().parents[3]  # repo top
skills/assess/tests/test_structure_drift.py:480:    repo_root = Path(__file__).resolve().parents[3]
skills/assess/tests/test_structure_drift.py:825:    repo_root = Path(__file__).resolve().parents[3]
```

`parents[3]` counts `tests/` (or `scripts/`) then `assess/` then `skills/` to reach the repo root today; under `plugins/assess/skills/assess/` the same three hops land on `plugins/assess/`, so all ten sites resolve to the plugin root instead. The two that read `.claude-plugin/plugin.json` would then find the assess plugin's own manifest, correct by coincidence of depth rather than by intent, and `test_action_contract.py:18` would look for `plugins/assess/action.yml`, which does not exist.

The `/assess` self-run's own seam allowlist is repo-relative:

```
$ sed -n '517,521p' skills/assess/scripts/lib/structure_drift.py
SEAM_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("skills/assess/scripts/lib", "skills/assess/tests"),
    ("scripts", "skills"),
)
```

The CI and action entry points:

```
$ rg -n 'working-directory' action.yml
89:      working-directory: ${{ github.action_path }}/skills/assess
113:      working-directory: ${{ github.action_path }}/skills/assess

$ rg -n 'working-directory' .github/workflows/tests.yml
34:        working-directory: skills/assess
47:        working-directory: scripts
80:        working-directory: skills/assess
84:        working-directory: scripts
90:        working-directory: skills/assess

$ rg -n '^\s+name:' .github/workflows/tests.yml
18:    name: skills/assess pytest
37:    name: scripts/ pytest
50:    name: plugin contract pytest
62:    name: ruff + mypy gates
```

Three of those four job `name:` literals are required status-check contexts on `main` (measured in [../floor/spec.md](../floor/spec.md): `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, `Validate PR title`, `floor enforcement`, `floor self-anchor`). D7 keeps every literal unchanged, so the `skills/assess pytest` job keeps its name while its `working-directory` moves.

The gate template stamped into adopter repos, and the standalone pipeline:

```
$ rg -n 'bjcoombs/ai-native-toolkit@' skills/assess/templates/assess-gate.yml.template
13:# The gate logic lives in the action (bjcoombs/ai-native-toolkit@v$plugin_version),
52:        uses: bjcoombs/ai-native-toolkit@v$plugin_version

$ rg -n '"source_dir"|_plugin_version|plugin.json' scripts/standalone_skill_config.py | head -12
23:def _plugin_version() -> str:
24:    """Read the canonical version from .claude-plugin/plugin.json.
30:    plugin_json = Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
31:    return json.loads(plugin_json.read_text("utf-8"))["version"]
34:VERSION = _plugin_version()
55:        "source_dir": "skills/assess",
152:        "source_dir": "skills/huddle",
215:        "source_dir": "skills/skill-forge",
250:        "source_dir": "skills/deslop",
279:        "source_dir": "skills/semantic-compress",

$ rg -n 'paths:|plugin.json' .github/workflows/build-standalone-skills.yml | head -5
6:    paths:
7:      - .claude-plugin/plugin.json
28:          old=$(git show "$BEFORE:.claude-plugin/plugin.json" 2>/dev/null \
31:          new=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json')).get('version',''))")
54:          VERSION=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")
```

`scripts/build-standalone-skills.sh` hard-codes no family path of its own. It reads every source directory from `standalone_skill_config.py` (`:69`, `skill_source_dir=repo_root / cfg["source_dir"]`), so PR1 gives it no edit:

```
$ rg -c 'skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b' scripts/build-standalone-skills.sh; echo "rc=$?"
rc=1
```

Four consumers sit outside the CI-run set above, and seventeen more references are skills citing each other. The pattern below is Requirement 10's residue backstop without its file exclusions: two lookbehinds skip a match that already carries a `plugins/<family>/` prefix, so it reads the same before and after the move.

```
$ RESIDUE='(?<!plugins/assess/|plugins/huddle/|plugins/deslop/|plugins/skill-craft/|plugins/gh-org/|plugins/delivery/|plugins/ai-native-toolkit/)skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)[a-zA-Z0-9/._-]*'

$ rg -noP "$RESIDUE" skills/semantic-compress/ skills/ab-equivalence/references/runner-prompt.md \
    skills/marathon/forge/forge-report.md skills/marathon/SKILL.md skills/assess-findings/SKILL.md \
    docs/huddle-explainer/README.md .github/workflows/claude-review.yml | sort
.github/workflows/claude-review.yml:64:skills/assess/
docs/huddle-explainer/README.md:5:skills/huddle/SKILL.md
docs/huddle-explainer/README.md:5:skills/huddle/SKILL.md
skills/ab-equivalence/references/runner-prompt.md:144:skills/semantic-compress/references/distill-loop.md
skills/assess-findings/SKILL.md:103:skills/assess/
skills/marathon/forge/forge-report.md:38:skills/marathon/forge/corpus.md
skills/marathon/forge/forge-report.md:92:skills/marathon/SKILL.md
skills/marathon/SKILL.md:506:skills/marathon/SKILL.md
skills/marathon/SKILL.md:506:skills/pr-review-merge/SKILL.md
skills/semantic-compress/references/cognitive-ergonomics.md:11:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/references/directive-clarity-rewrites.md:14:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/references/distill-loop.md:3:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/references/distillation-report-template.md:111:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/references/transfer-set-design.md:100:skills/ab-equivalence/references/runner-prompt.md
skills/semantic-compress/references/transfer-set-design.md:5:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/references/transfer-set-design.md:5:skills/skill-forge/references/test-taxonomy.md
skills/semantic-compress/SKILL.md:124:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/SKILL.md:186:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/SKILL.md:199:skills/ab-equivalence/references/ab-equivalence.md
skills/semantic-compress/SKILL.md:207:skills/ab-equivalence/references/ab-equivalence.md
```

Seventeen of those twenty matches, on fifteen lines, are in-skill self-references that move with their own skill; the other three are `.github/workflows/claude-review.yml:64` and the two on `docs/huddle-explainer/README.md:5`, one of them a relative link `test_internal_links_resolve` follows. Requirement 10 assigns each its edit.

Two files hold the required-status-check literal `skills/assess pytest` and no family path at all, so a residue grep that reads it as a path is asking PR1 to break a branch-protection context:

```
$ rg -nP 'skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b' scripts/tests/test_floor_anchor.py
93:        "skills/assess pytest",

$ git ls-files .assess | wc -l
22
```

Requirement 10's backstop, run with its four exemptions over this pre-move tree, returns 111 matches in 40 files. Every one is a consumer commit 2 rewrites or a file it deletes, which is why the post-PR1 expectation is zero:

```
$ BACKSTOP='(?<!plugins/assess/|plugins/huddle/|plugins/deslop/|plugins/skill-craft/|plugins/gh-org/|plugins/delivery/|plugins/ai-native-toolkit/)(?<!CLAUDE_PLUGIN_ROOT/|\.claude/)skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b(?! pytest)'

$ rg -nP --no-heading \
    -g '!docs/design/**' -g '!docs/superpowers/**' -g '!.assess/**' -g '!*.svg' \
    -g '!skills/assess/tests/fixtures/**' -g '!plugins/assess/skills/assess/tests/fixtures/**' \
    -g '!FLOOR.md' -g '!scripts/floor_check.py' -g '!scripts/floor_anchor.py' \
    -g '!.github/workflows/floor.yml' -g '!docs/floor-anchor-proof.md' \
    "$BACKSTOP" . | wc -l
111

$ rg -lP \
    -g '!docs/design/**' -g '!docs/superpowers/**' -g '!.assess/**' -g '!*.svg' \
    -g '!skills/assess/tests/fixtures/**' -g '!plugins/assess/skills/assess/tests/fixtures/**' \
    -g '!FLOOR.md' -g '!scripts/floor_check.py' -g '!scripts/floor_anchor.py' \
    -g '!.github/workflows/floor.yml' -g '!docs/floor-anchor-proof.md' \
    "$BACKSTOP" . | wc -l
40
```

The six hand-rolled `SKILL_DIR` sites and the report footer:

```
$ rg -n 'SKILL_DIR="\$\{CLAUDE_PLUGIN_ROOT' skills/ | sort
skills/assess-pr/SKILL.md:271:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/assess/SKILL.md:290:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/assess/SKILL.md:364:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/assess/SKILL.md:459:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
skills/ghreport/SKILL.md:41:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/ghreport}"
skills/ghsync/SKILL.md:32:SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/ghsync}"

$ rg -c '/ai-native-toolkit:assess' skills/assess-findings/SKILL.md skills/assess-pr/SKILL.md | sort
skills/assess-findings/SKILL.md:1
skills/assess-pr/SKILL.md:1
```

`ghreport.sh` resolves `ghsync` by sibling directory name, which is why D4 refuses to rename either:

```
$ sed -n '282,286p' skills/ghreport/scripts/ghreport.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHSYNC_SH="$SCRIPT_DIR/../../ghsync/scripts/ghsync.sh"
if [ ! -f "$GHSYNC_SH" ]; then
    GHSYNC_SH="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills}/ghsync/scripts/ghsync.sh"
fi
```

Under `plugins/gh-org/skills/{ghsync,ghreport}/` the sibling hop still resolves; the `CLAUDE_PLUGIN_ROOT` fallback on `:285` does not. R4 (WS3, [../frontmatter/spec.md](../frontmatter/spec.md)) owns all six `SKILL_DIR` sites and that fallback; WS1 moves them unchanged.

### The contract test's discovery functions

```
$ rg -n 'REPO = |^SKILLS|^COMMANDS|^AGENTS|^PLUGIN|def skill_dirs|def command_files|def shipped_md|def all_authored_markdown|def known_agent_names|def test_marketplace_entries_exist|def test_subagent_types_resolve' tests/test_plugin_contract.py
12:REPO = Path(__file__).resolve().parent.parent
13:SKILLS = REPO / "skills"
14:COMMANDS = REPO / "commands"
15:AGENTS = REPO / "agents"
16:PLUGIN = REPO / ".claude-plugin"
69:def skill_dirs():
75:def command_files():
79:def shipped_md():
83:def all_authored_markdown():
108:def known_agent_names():
156:def test_subagent_types_resolve(p):
191:def test_marketplace_entries_exist():
```

`skill_dirs()` (`:69-72`) iterates `SKILLS` one level deep and keeps directories holding a `SKILL.md`; `command_files()` (`:75-76`) globs `commands/*.md` and returns `[]` once the directory is gone. `test_marketplace_entries_exist` (`:191-201`) asserts only that each entry's `source` exists on disk and skips a missing `marketplace.json`. R19 guard 6 in PR0 ([../floor/spec.md](../floor/spec.md)) already converts discovery to `rglob` and re-parametrizes `test_subagent_types_resolve` over `shipped_md()`, so WS1 inherits both.

### `.gitignore` is an allowlist that does not name `plugins/`

```
$ sed -n '1,18p;51,52p;56p' .gitignore
# Ignore everything
/*

# But not these files and directories
!/.gitignore
!/.claude-plugin/
!/agents/
!/commands/
!/skills/
!/LICENSE
!/FLOOR.md
!/docs/
!/scripts/
!/tests/
!/dist/
dist/standalone-skills/
!/.github/
!/action.yml
!/skills/assess/tests/fixtures/lean_with_skills/CLAUDE.md
!/skills/assess/tests/fixtures/lean_with_skills/.claude/
!/skills/skill-forge/tests/fixtures/flawed-instruction-file/CLAUDE.md

$ sed -n '44,45p' .gitignore
.claude/
**/CLAUDE.md
```

Three re-include lines carry an absolute old path, not two, and all three sit under the blanket rules on lines 44-45. The failure this creates is silent, measured on a scratch repository with the same allowlist shape:

```
$ cd /tmp/mvprobe && git init -q . && printf '/*\n!/.gitignore\n!/skills/\n' > .gitignore \
    && mkdir -p skills/a && echo hi > skills/a/SKILL.md && git add -A && git commit -qm init

$ mkdir -p plugins/p/skills && git mv skills/a plugins/p/skills/a; echo "rc=$?"
rc=0

$ echo new > plugins/p/README.md && git add -A; echo "rc=$?"
rc=0

$ git status --short
R  skills/a/SKILL.md -> plugins/p/skills/a/SKILL.md

$ git check-ignore -v plugins/p/README.md
.gitignore:1:/*	plugins/p/README.md
```

A `git mv` into the un-allowlisted `plugins/` prefix succeeds and stages the rename, because tracked files stay tracked. A *new* file under `plugins/` is ignored by `/*`, and `git add -A` exits 0 having staged nothing. So the moves survive without a `.gitignore` change and the new manifests, READMEs and symlinks do not.

### What the move does not break

```
$ rg -c 'skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b' \
    skills/assess/tests/fixtures/golden/run-context-baseline.json \
    skills/assess/tests/fixtures/golden/assess-report-baseline.md
skills/assess/tests/fixtures/golden/assess-report-baseline.md:23
skills/assess/tests/fixtures/golden/run-context-baseline.json:318
```

`skills/assess/tests/fixtures/golden/run-context-baseline.json` holds 318 lines matching a family skill path and `assess-report-baseline.md` holds 23, but neither is regenerated against this tree: `golden.py:114-123` loads both as stored inputs and `test_golden_baseline.py` asserts only their structure (block presence, sentinel normalization, finding order). `test_decomposition_parity.py` builds its own synthetic repository in `tmp_path`, so the stale paths inside the baselines fail no test. The two things in that file that do move are its `REPO_ROOT` (`:30`) and `ASSESS_SKILL` (`:31`).

`.github/workflows/claude-review.yml:35` checks out `${{ github.event.repository.default_branch }}` for the bot's instructions, so PR1 is reviewed against the pre-move `.github/claude-review-instructions.md`, which itself holds 6 hard-coded family paths.

```
$ rg -c 'skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b' .github/claude-review-instructions.md
6
```

## Design

### Inherited decisions

D1 fixes six family plugins under `plugins/<name>/`, each its own marketplace entry with a plain `./plugins/<name>` source and no `metadata.pluginRoot`. D2 fixes `assess` at 1.57.0, the other five and the umbrella at 2.0.0, and no `version` field on any marketplace entry. D5 fixes the symlink meta-plugin. D7 keeps `action.yml` at the repo root and every CI job `name:` literal unchanged. D8 fixes the two-commit shape. D4 and D9 bear on WS2 and WS4. None is re-argued here.

### The marketplace

`.claude-plugin/marketplace.json` gains a marketplace `description` (without it, `--strict` is a hard error, measured above) and seven entries:

```json
{
  "name": "ai-native-toolkit",
  "description": "Skills for AI-native development, split into opt-in family plugins: codebase assessment, Six Hats deliberation, AI-writing cleanup, skill authoring, GitHub org operations, and delivery workflow.",
  "owner": {
    "name": "Ben Coombs"
  },
  "plugins": [
    { "name": "assess", "source": "./plugins/assess", "description": "Score a codebase's readiness for AI agent contributors against the 0-8 layered contract model; emit a complexity hotspot SVG and a doc-navigability graph." },
    { "name": "huddle", "source": "./plugins/huddle", "description": "Structured multi-perspective deliberation using Six Thinking Hats with Fibonacci team sizing, plus deep understanding mode." },
    { "name": "deslop", "source": "./plugins/deslop", "description": "Detect and remove the telltale signs of AI writing from prose." },
    { "name": "skill-craft", "source": "./plugins/skill-craft", "description": "Author, harden and compress skills and agent instruction files, gated on A/B behavioural equivalence." },
    { "name": "gh-org", "source": "./plugins/gh-org", "description": "Bulk clone, sync and report on every GitHub repo you can access across an org or personal account." },
    { "name": "delivery", "source": "./plugins/delivery", "description": "Personal delivery workflow: Task Master orchestration, GitHub-issue marathons, and autonomous PR and branch fix loops." },
    { "name": "ai-native-toolkit", "source": "./plugins/ai-native-toolkit", "description": "Umbrella plugin: loads every family under the original names and the /ai-native-toolkit: namespace. Install the families you use and uninstall this instead." }
  ]
}
```

### The plugin and component table

| Plugin | Version | Skills | Agents |
|--------|---------|--------|--------|
| `assess` | 1.57.0 | `assess`, `assess-findings`, `assess-pr` | `assess-layer-scorer` |
| `huddle` | 2.0.0 | `huddle`, `6hats` (WS2 stub), `understand` | `white-hat`, `red-hat`, `black-hat`, `yellow-hat`, `green-hat`, `blue-hat`, `scribe` |
| `deslop` | 2.0.0 | `deslop` (plugin-root `SKILL.md`) | - |
| `skill-craft` | 2.0.0 | `skill-forge`, `semantic-compress`, `ab-equivalence` | - |
| `gh-org` | 2.0.0 | `ghsync`, `ghreport` | - |
| `delivery` | 2.0.0 | `tm`, `issues`, `fix-pr`, `fix-develop`, `marathon`, `pr-review-merge` | - |
| `ai-native-toolkit` | 2.0.0 | symlinks to all 18 above | symlinks to all 8 above |

`tm-marathon-config-example` is not in the table: it becomes `plugins/delivery/skills/marathon/references/config-example.md` and stops being a component (R2). Each family plugin carries `.claude-plugin/plugin.json` with `name`, `displayName`, `description`, `version`, `license`, `author`, `homepage`, `repository`, `keywords`, plus a `README.md` at the plugin root carrying install and usage.

`deslop` is the one plugin whose single skill sits at the plugin root rather than under `skills/`, per R1. That shape costs the umbrella a special case: seventeen of the eighteen skills are linked as one directory symlink each, while `deslop` needs `plugins/ai-native-toolkit/skills/deslop/` to be a real directory holding a `SKILL.md` symlink and a `references` symlink, because a directory symlink to `plugins/deslop/` would drag that plugin's own `plugin.json` and `README.md` into a skill directory. Verification gate 2 decides whether the shape is worth the case, against Requirement 3's per-family inventory assertion: if `claude plugin details deslop` does not list exactly one skill named `deslop`, the move target becomes `plugins/deslop/skills/deslop/SKILL.md`, the same shape as every other family, the umbrella's special case disappears, and nothing else in this spec changes.

### The two-commit shape (D8)

**Commit 1 is a bare `git mv` and nothing else**, so every marked file reads R100 and the rename-aware floor PR0 introduces passes with zero floor edits. `git mv` needs its destination directory to exist, so each family's `mkdir -p` precedes its moves. The complete list, 28 moves:

```
plugins/assess:      skills/assess, skills/assess-findings, skills/assess-pr
                     -> plugins/assess/skills/<same>
                     agents/assess-layer-scorer.md -> plugins/assess/agents/assess-layer-scorer.md
plugins/huddle:      skills/huddle -> plugins/huddle/skills/huddle
                     commands/6hats.md      -> plugins/huddle/skills/6hats/SKILL.md
                     commands/understand.md -> plugins/huddle/skills/understand/SKILL.md
                     agents/{white,red,black,yellow,green,blue}-hat.md, agents/scribe.md
                     -> plugins/huddle/agents/<same>
plugins/deslop:      skills/deslop/SKILL.md   -> plugins/deslop/SKILL.md
                     skills/deslop/references -> plugins/deslop/references
plugins/skill-craft: skills/skill-forge, skills/semantic-compress, skills/ab-equivalence
                     -> plugins/skill-craft/skills/<same>
plugins/gh-org:      skills/ghsync, skills/ghreport -> plugins/gh-org/skills/<same>
plugins/delivery:    skills/marathon, skills/pr-review-merge
                     -> plugins/delivery/skills/<same>
                     commands/tm.md          -> plugins/delivery/skills/tm/SKILL.md
                     commands/issues.md      -> plugins/delivery/skills/issues/SKILL.md
                     commands/fix-pr.md      -> plugins/delivery/skills/fix-pr/SKILL.md
                     commands/fix-develop.md -> plugins/delivery/skills/fix-develop/SKILL.md
                     commands/tm-marathon-config-example.md
                     -> plugins/delivery/skills/marathon/references/config-example.md
```

The four marked files are `skills/marathon/SKILL.md`, `skills/pr-review-merge/SKILL.md`, `commands/tm.md` and `commands/issues.md`. All four land on `plugins/delivery/skills/<x>/SKILL.md`, which is one of the three destinations the floor's structural check accepts ([../floor/spec.md](../floor/spec.md), "Rename mapping"). Because commit 1 edits no byte of any of them and commit 2 leaves them alone, the whole-PR diff maps all four at R100 and no floor sign-off is triggered: PR1 changes no floor core file and no floor token.

After commit 1, `skills/`, `commands/` and `agents/` still hold exactly their three `README.md` files and nothing else.

**Commit 2 carries every consumer**, in six groups: the seven new `plugin.json` manifests and seven plugin `README.md` files; the meta-plugin's symlinks; the rewritten `.claude-plugin/marketplace.json` and the deletion of the root `plugin.json`; the `.gitignore` allowlist rewrite (which must land before any new file under `plugins/` is staged, per the measurement above); every hard-coded path from the Current state inventory; and the R11 assertions plus the R3 contract test. The three `README.md` files leave with it, folding their content into the plugin READMEs, which empties and removes `skills/`, `commands/` and `agents/`. WS2's `6hats` stub body and `huddle` `argument-hint` ride the same commit ([../naming/spec.md](../naming/spec.md)).

### The umbrella, and what the probes did and did not settle

D5 is Decided. Probe 1, recorded in [../probes.md](../probes.md), installed a two-plugin marketplace from a GitHub branch and reports that a meta-plugin whose `skills/<x>` **and** `agents/<x>.md` entries are symlinks into a sibling plugin lists both components exactly once (`Skills (1)  probeskill`, `Agents (1)  probe-agent`, exit 0), with the cache copy holding the dereferenced targets as regular files rather than a whole-repo copy. The `agents/` dereference that the plugins-reference does not document therefore behaves as `skills/` does, and the fallback D5 names (a hybrid root entry with `strict: false`) is not needed.

Probe 1's own Conclusion records what it did not exercise: name resolution. The only registry ids captured anywhere in `probes.md` are namespaced (`ant-assess:probe-agent`, `ant-umbrella:probe-agent`), no bare `probe-agent` entry was reported, and no bare `subagent_type` lookup was invoked. Skill bodies in this repo use bare `subagent_type` names (`white-hat`, `assess-layer-scorer`), so **bare agent-name resolution under the umbrella is a WS1 verification item**, not an inherited fact: Verification gate 5 below resolves a bare `subagent_type` from a GitHub-sourced marketplace install of the meta-plugin and records the verdict in the PR.

Probe 2, also recorded in `../probes.md`, reports that a family installed beside the umbrella double-registers silently: both plugins load, the loader reports `0 duplicate/user-owned entries skipped`, and each plugin's `details` charges its own always-on cost. That is the fact R20's notice text mitigates by naming the order - install families first, then uninstall the umbrella.

### Platform features this design rests on, and where each is documented

- **Symlink dereference inside a marketplace.** plugins-reference, "Share files within a marketplace with symlinks": a meta-plugin's `skills/` directory may link to skills defined by other plugins in the marketplace. The `agents/` half is undocumented and is settled by Probe 1 instead.
- **Local installs skip out-of-tree symlinks.** The reason `claude --plugin-dir plugins/ai-native-toolkit` cannot verify the umbrella and a GitHub-sourced marketplace must (D5; exercised that way by Probe 1).
- **`claude plugin validate --strict`.** Measured `--help` above: treats warnings as errors for CI.
- **Marketplace entry `strict`.** plugin-marketplaces, marketplace schema. Not used: D1 rejects the marketplace-root shared-`skills/` layout that needs `strict: false` per entry, and D5 rejects the hybrid root entry for the same reason.
- **Marketplace `renames`.** plugin-marketplaces, plugin renames and removals. Not used by WS1; WS10 ([../sunset/spec.md](../sunset/spec.md)) adds `{"ai-native-toolkit": null}` when the umbrella is retired at 3.0.0.
- **`${CLAUDE_SKILL_DIR}`.** skills, script paths in skill markdown: Claude Code text-substitutes it in a `SKILL.md` body. WS3 ([../frontmatter/spec.md](../frontmatter/spec.md)) adopts it; WS1 only moves the sites that hand-roll `SKILL_DIR`.
- **Bare `subagent_type` for plugin agents.** sub-agents, invoking a plugin's subagents: pass only the agent name and Claude Code finds it. This is the default WS1 keeps, and the claim Verification gate 5 tests under the umbrella.
- **Commands merged into skills.** skills, and plugins-reference's `commands/` entry ("Skills as flat Markdown files. Use `skills/` for new plugins"). R2's basis; the two forms are documented as behaviourally identical, which is what makes commit 1 a pure move.

## Requirements

Every path below is a POST-move path (`plugins/<family>/...`); this is stated once and applies throughout.

1. `.claude-plugin/marketplace.json` carries a `description` and exactly seven entries with `source: "./plugins/<name>"`, no `version` field and no `metadata.pluginRoot`. The root `.claude-plugin/plugin.json` is deleted. Verifiable: `jq '.plugins | length'` returns 7; `jq '[.plugins[] | select(has("version") or (.metadata? // {} | has("pluginRoot")))] | length'` returns 0; `test -e .claude-plugin/plugin.json` fails.
2. `claude plugin validate .` passes with zero warnings, and `claude plugin validate plugins/<name> --strict` passes for all seven plugins. Verifiable: a new CI job runs both and fails on a non-zero exit.
3. `claude plugin details <name>` for each family lists exactly the components in the Design table: no `README`, no `tm-marathon-config-example`. Verifiable: the pasted output per family in the PR body (Verification gate 2).
4. The `assess` plugin's reported always-on cost is under 600 tokens. Verifiable: the `Always-on:` line of `claude plugin details assess`, pasted in the PR; the baseline is ~490 measured above and WS1 adds no component to that family.
5. `commands/`, `agents/README.md`, `commands/README.md` and `skills/README.md` no longer exist, and no non-component `.md` sits in a component slot. Verifiable: a new `test_no_non_components_in_component_dirs` in `tests/test_plugin_contract.py` asserts no `.md` file sits directly in any `plugins/*/skills/` directory and that every `.md` directly in a `plugins/*/agents/` directory parses as an agent (frontmatter with `name` and `description`). Those two are the loader's component slots, so a `references/` or `scripts/` file deeper in a skill directory, and `plugins/*/README.md`, stay legal.
6. Each of the seven plugins has `.claude-plugin/plugin.json` with `name`, `displayName`, `description`, `version`, `license`, `author`, `homepage`, `repository`, `keywords`, and a `README.md` at the plugin root. `assess` is at `1.57.0`; the other six are at `2.0.0`. Verifiable: a contract test reads the seven manifests and asserts the field set and the two versions.
7. Every former `commands/*.md` file is a `plugins/<family>/skills/<name>/SKILL.md` keeping its `name`, `disable-model-invocation: true`, `description` and (where present) `argument-hint`, and `tm-marathon-config-example.md` is `plugins/delivery/skills/marathon/references/config-example.md`. Verifiable: `git log --follow` on each new path shows the rename; `test_skill_frontmatter` passes for the six new skill directories; `rg -c 'name: tm-marathon-config-example' plugins/` returns nothing.
8. `plugins/ai-native-toolkit/` holds its own `plugin.json` (name `ai-native-toolkit`, version 2.0.0) and links every family component into its `skills/` and `agents/` directories, committed as git mode `120000`. No `dependencies`, no `strict`, no root manifest. Verifiable: every blob git tracks under `plugins/ai-native-toolkit/skills/` and `plugins/ai-native-toolkit/agents/` has mode `120000` - `git ls-files -s plugins/ai-native-toolkit/skills plugins/ai-native-toolkit/agents | rg -cv '^120000'` returns 0 - and Verification gate 4's `details` paste lists 18 skills and 8 agents.
9. `test_marketplace_entries_exist` additionally asserts, for the meta-plugin entry, that every symlink under `plugins/ai-native-toolkit/` resolves to an existing file. Verifiable: the test fails when a symlink target is renamed and passes otherwise, exercised by a unit case using `tmp_path`.
10. Every consumer measured in Current state resolves post-move: the five `tests/*.py` path constants, the four `scripts/tests/*` paths, the ten `parents[3]` sites (replaced with explicit repo-root discovery rather than a deeper index), `SEAM_ALLOWLIST`, `action.yml:89/:113`, `tests.yml:34,47,80,84,90`, `standalone_skill_config.py`'s five `source_dir` values and its three `bundle_files` sources, the `build-standalone-skills.yml` `paths:` trigger and its three `plugin.json` reads, `.github/claude-review-instructions.md`, `docs/index.md`, `README.md`, `CLAUDE.md`, `docs/testing-a-branch-locally.md`.

    Eight consumers sit outside that set, each with its own edit or an explicit reason it needs none:

    | Consumer | Edit in commit 2 |
    |----------|------------------|
    | `docs/huddle-explainer/README.md:5` | the relative link `../../skills/huddle/SKILL.md` becomes `../../plugins/huddle/skills/huddle/SKILL.md`, and the inline mention on the same line with it. Without the edit Requirement 16's `test_internal_links_resolve` is red |
    | `.github/workflows/claude-review.yml:64` | "deterministic core under `skills/assess/`" becomes `plugins/assess/skills/assess/`. The bot reads this file from the default branch (`:35`), so the correction takes effect for the PR after PR1, not for PR1 |
    | `docs/floor-anchor-proof.md` | none. It records a proof already run against the pre-move tree, so its paths are historical, exempt for the same reason as the golden baselines |
    | `scripts/tests/test_floor_anchor.py:93` | none. Its one match is the required-context literal `skills/assess pytest`, a check name and not a path, which D7 and Requirement 17 keep |
    | `skills/assess/scripts/lib/README.md:1,37,38,43,44` | the five prose paths become `plugins/assess/skills/assess/...`. This file is the repository's module-ownership declaration and `/assess` parses it as data, so it moves with the skill and its declared paths are rewritten in the same commit |
    | `skills/assess/tests/test_ownership_parser.py:369,379,385` | the docstring path and the two string literals take the same rewrite. `test_integration_parses_lib_readme_seam_declaration` parses the real `skills/assess/scripts/lib/README.md` and asserts its declared `doc_graph.py` path resolves to a tracked file, so the README prose and these literals must move together or the required `skills/assess pytest` context goes red |
    | `skills/assess/tests/test_structure_drift.py:248,249,258,390,391,475,488-491,627,683` | the same rewrite. `test_integration_lib_readme_seam_paths_are_not_false_positives` (`:258`) asserts the README's declared seam paths produce no `empty_ownership_patterns`; `test_tier1_integration_no_false_positives_after_allowlist` (`:488-491`) asserts the real lib to tests seam is absorbed by the allowlist; the two synthetic seam pairs (`:390,391` and `:683`) must straddle the post-move `SEAM_ALLOWLIST`. Same red job otherwise |
    | `skills/assess/tests/test_assess_report.py:91,416` | no functional edit. Both matches are one synthetic fixture string (`../skills/marathon/SKILL.md`) built inside the test and asserted in the formatter's output; nothing resolves it against the tree, so it is correct either side of the move. Commit 2 respells it anyway, so the backstop reaches 0 without a fifth exemption |

    The seventeen in-skill self-references measured in Current state, on fifteen lines, move with their own skill and are re-pointed in the same commit: `skills/semantic-compress/SKILL.md:124,186,199,207` and its five `references/` files cite `ab-equivalence` and `skill-forge`, and `skills/ab-equivalence/references/runner-prompt.md:144` cites `semantic-compress`, all three skills landing under `plugins/skill-craft/`; `skills/marathon/SKILL.md:506` and `skills/marathon/forge/forge-report.md:38,92` cite `marathon` and `pr-review-merge`, both under `plugins/delivery/`; `skills/assess-findings/SKILL.md:103` cites `assess`, under `plugins/assess/`.

    The remaining matches are prose and comments rewritten with their own file in commit 2: `skills/assess/tests/README.md:1,70`, `scripts/pyproject.toml:21`, `skills/assess/scripts/doc-graph-svg.py:33`, `skills/assess/scripts/lib/ci_workflow.py:19`, `skills/assess/scripts/lib/ownership_parser.py:70,313`, `skills/assess/tests/test_self_architecture.py:4,5`, `skills/assess/tests/test_accretion_ratchet.py:376`, `skills/assess/tests/test_doc_graph.py:233`, `skills/assess/tests/test_uninstall.py:9`, plus four path-derivation comments in files the paragraph above already names (`skills/assess/scripts/assess_core.py:345`, `skills/assess/scripts/complexity-treemap.py:63,543`, `skills/assess/tests/test_decomposition_parity.py:29`), each describing the `parents[3]` hop count the same commit replaces.

    Those 111 in 40 files sum as follows. The backstop runs without `-o`, so each count is matching lines:

    | Group | Files | Lines |
    |-------|-------|-------|
    | Named consumers in the paragraph above: `docs/index.md` 12, `scripts/standalone_skill_config.py` 8, `CLAUDE.md` 7, `.github/workflows/tests.yml` 6, `.github/claude-review-instructions.md` 5, `docs/testing-a-branch-locally.md` 4, `scripts/tests/test_transform.py` 3, `.gitignore` 3 (R12), `skills/assess/scripts/lib/structure_drift.py` 2 (`SEAM_ALLOWLIST` and the comment above it), `action.yml` 2 | 10 | 52 |
    | Files commit 2 deletes: `commands/README.md` 2, `agents/README.md` 2 (R5) | 2 | 4 |
    | The table above: `docs/huddle-explainer/README.md` 1, `.github/workflows/claude-review.yml` 1. Its `docs/floor-anchor-proof.md` and `scripts/tests/test_floor_anchor.py` rows contribute nothing, hidden by the proof glob and the `(?! pytest)` lookahead | 2 | 2 |
    | In-skill self-references, the fifteen lines listed above | 10 | 15 |
    | Path-derivation comments in the named `parents[3]` files | 3 | 4 |
    | The lib README seam set: `skills/assess/scripts/lib/README.md` 5, `test_ownership_parser.py` 3, `test_structure_drift.py` 12 | 3 | 20 |
    | The remaining prose and comments listed above | 9 | 12 |
    | Synthetic fixture string, no functional edit: `test_assess_report.py` 2 | 1 | 2 |
    | **Total** | **40** | **111** |

    Verifiable: the three required pytest jobs are green, and the residue backstop returns nothing. The backstop is the Current state path grep plus four exemptions, run exactly as written here:

    ```
    $ BACKSTOP='(?<!plugins/assess/|plugins/huddle/|plugins/deslop/|plugins/skill-craft/|plugins/gh-org/|plugins/delivery/|plugins/ai-native-toolkit/)(?<!CLAUDE_PLUGIN_ROOT/|\.claude/)skills/(assess|huddle|deslop|ghsync|ghreport|marathon|pr-review-merge|skill-forge|semantic-compress|ab-equivalence|assess-findings|assess-pr)\b(?! pytest)'

    $ rg -nP --no-heading \
        -g '!docs/design/**' -g '!docs/superpowers/**' -g '!.assess/**' -g '!*.svg' \
        -g '!skills/assess/tests/fixtures/**' -g '!plugins/assess/skills/assess/tests/fixtures/**' \
        -g '!FLOOR.md' -g '!scripts/floor_check.py' -g '!scripts/floor_anchor.py' \
        -g '!.github/workflows/floor.yml' -g '!docs/floor-anchor-proof.md' \
        "$BACKSTOP" . | wc -l
    ```

    The expected count after PR1 is 0. It is 111 in 40 files on the pre-move tree (measured in Current state), and the table above accounts for every one of them: each is a line commit 2 rewrites or a line in a file it deletes, the two in `test_assess_report.py` being a synthetic string respelled for the count rather than for correctness. Each exemption exists because PR1 may not or need not clear what it hides:

    - **The post-move prefix**, the two lookbehinds. Every family keeps its directory name under `plugins/<family>/skills/<name>/`, so the bare Current state pattern matches its own correct destination and can never reach zero: run as stated it returns 378 today, and after a flawless PR1 it would still match every correctly rewritten `plugins/<family>/skills/<name>/` path. The `${CLAUDE_PLUGIN_ROOT}/skills/<x>` and `~/.claude/skills/<x>` forms are plugin-root-relative and user-install paths, correct post-move and unchanged by WS1, which is why R4 (WS3) rather than PR1 owns the six `SKILL_DIR` sites.
    - **Check names, not paths**, the `(?! pytest)` lookahead. `.github/workflows/tests.yml:18` (`name: skills/assess pytest`) and `scripts/floor_anchor.py:97` (the same string as a required-status-check context) are branch-protection contexts, and Requirement 17 and D7 forbid changing either. The lookahead excludes the string `skills/assess pytest` and nothing else, so it also covers the same literal in `CLAUDE.md`, `.github/claude-review-instructions.md`, `scripts/tests/test_floor_anchor.py:93` and `docs/floor-anchor-proof.md`.
    - **Generated artefacts**, `-g '!.assess/**' -g '!*.svg'`. The 22 tracked files under `.assess/` are `/assess` self-run outputs, regenerated by Verification gate 7 after merge rather than hand-edited by PR1. This is the exemption the golden baselines already carry.
    - **Floor core and its proof**, the last five globs. Requirement 17 forbids PR1 touching `FLOOR.md`, `scripts/floor_check.py`, `scripts/floor_anchor.py` and `.github/workflows/floor.yml`; PR0 owns their contents and deletes `MARKED_FILES` outright ([../floor/spec.md](../floor/spec.md)). `docs/floor-anchor-proof.md` is the historical record above.

11. `test_action_contract.py` asserts every `working-directory` in `action.yml` exists on disk. Verifiable: the assertion is the R19 guard PR0 lands ([../floor/spec.md](../floor/spec.md), guard 1); PR1 inherits it and it goes red on a stale path.
12. `.gitignore` allowlists `!/plugins/`, drops `!/agents/`, `!/commands/` and `!/skills/`, and rewrites all three fixture re-includes to their post-move paths. Verifiable: `git check-ignore -v plugins/assess/.claude-plugin/plugin.json` exits 1, and `git status --porcelain --ignored | rg 'fixtures/(lean_with_skills|flawed-instruction-file)'` returns nothing.
13. The report footer in `plugins/assess/skills/assess-findings/SKILL.md` and `plugins/assess/skills/assess-pr/SKILL.md` cites bare `/assess` for new reports. Verifiable: `rg -c '/ai-native-toolkit:assess' plugins/assess/skills/` returns nothing; committed reports in adopter repos keep resolving through the umbrella (D5).
14. `docs/migration-2.0.md` exists with the skeleton R18 requires: a path table (every `skills/<x>`, `commands/<x>.md` and `agents/<x>.md` to its new home), an install table (per-family install lines and the umbrella line), a version table (the seven plugins and their first versions), and a "Removed in 3.0" list seeded with the umbrella, the `6hats` stub and the assess-helpers fold. Verifiable: a contract test asserts the four headings exist and that the path table has one row per moved component.
15. `README.md` gains an "Upgrading from 1.x" block under Install carrying R20's text: `/plugin update ai-native-toolkit` keeps everything working; the six per-family install lines; install families before uninstalling the umbrella; the action pin and the standalone ZIPs are unaffected; `assess` stays on 1.x so Dependabot bumps stay MINOR and regression baselines stay comparable. `README.md:165`'s `/plugin remove` becomes `uninstall`, `README.md:140`'s `@v1.42.2` example pin is corrected, and the "Repository structure" tree at `README.md:366` shows `plugins/`. Verifiable: `rg -n '/plugin remove|@v1.42.2' README.md` returns nothing; the block links `docs/migration-2.0.md`.
16. Every relative link in the repository still resolves. Verifiable: `test_internal_links_resolve` is green, and `skill-forge`'s one cross-plugin relative link (`../assess-pr/SKILL.md` at `skills/skill-forge/SKILL.md:171`) becomes a URL or a bare mention, since `skill-craft` and `assess` are separate plugins post-move.
17. No file under `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml` or `FLOOR.md` is touched, and no CI job `name:` literal changes. Verifiable: `git diff --name-only origin/main` contains none of the four paths, and `rg -n '^\s+name:' .github/workflows/*.yml` is byte-identical to `origin/main`.

## Verification

1. **`ab-equivalence` freeze.** The transfer sets that exist in this checkout are `skills/marathon/forge/corpus.md` (five cases: `happy-1`, `edge-1`, `adv-1`, `comp-1`, `crash-1`) and `skills/skill-forge/tests/fixtures/{flawed-sample-skill,flawed-instruction-file}`. `skills/huddle/` holds only `SKILL.md` and has no transfer set, which is what D9 records and WS4 builds. PR1 runs `ab-equivalence` over the marathon corpus against `plugins/delivery/skills/marathon/SKILL.md` and records the per-case verdict in the PR body; commit 1 is byte-identical, so a non-equivalent verdict means commit 2 changed behaviour it was not meant to. No transfer set is invented for the other five families: their freeze is the byte-identity of commit 1, evidenced by `git diff --find-renames --diff-filter=R --summary` showing R100 for every moved file.
2. **`claude --plugin-dir plugins/<name>` per family.** Six runs, one per family plugin, each in a profile with no `~/.claude/skills/<name>` copy, pasting `claude plugin details <name>` and one bare invocation (`/assess`, `/huddle`, `/deslop`, `/skill-forge`, `/ghsync`, `/tm`). This is the gate that decides Requirement 3's per-family component inventory, the `deslop` plugin-root question the Design section leaves to it, and Requirement 4's token ceiling.
3. **`claude plugin validate` in CI.** A new job runs `claude plugin validate .` and `claude plugin validate plugins/<name> --strict` for all seven. It is added as a new job with a new `name:`; it is not a required context, so no branch-protection change is needed (D7).
4. **The meta-plugin, from a GitHub-sourced marketplace only.** `claude plugin marketplace add bjcoombs/ai-native-toolkit#<pr-branch>` then `claude plugin install ai-native-toolkit@ai-native-toolkit`, pasting `claude plugin details ai-native-toolkit`: 18 skills and 8 agents, each once, no `README`. `--plugin-dir` cannot do this (D5: local installs skip out-of-tree symlinks), which Probe 1 already worked around the same way.
5. **Bare `subagent_type` resolution under the umbrella.** In the same GitHub-sourced install, invoke a skill whose body dispatches a bare agent name (`white-hat` from `huddle`, `assess-layer-scorer` from `assess`) and paste the result. Probe 1 measured loading and inventory, not name resolution, and recorded no bare registry entry, so this is the open half of D5 and PR1 does not merge without the paste. If a bare name does not resolve, the fix is inside WS1's scope: the affected skill bodies qualify the `subagent_type` as `ai-native-toolkit:<agent>`.
6. **The three required pytest jobs and the two floor contexts**, green under their unchanged names, plus `Validate PR title`.
7. **`/assess` self-run on the post-merge tree**: instruction-file layer Present, no orphaned node introduced by the move, and no phantom seam from `SEAM_ALLOWLIST`. This is the post-merge self-test the tag waits on (Rollback).

## Breadcrumbs

| Breadcrumb | Where | `remove-in` |
|------------|-------|-------------|
| Symlink meta-plugin `ai-native-toolkit` keeping the plugin id, the `/ai-native-toolkit:<x>` namespace and every bare name | `plugins/ai-native-toolkit/` | 3.0.0, via marketplace `renames: {"ai-native-toolkit": null}` ([../sunset/spec.md](../sunset/spec.md)) |
| `6hats` stub forwarding to `huddle` | `plugins/huddle/skills/6hats/SKILL.md` (WS2) | 3.0.0 |
| Assess helpers not folded into `references/` (out of scope; would cut assess always-on from ~490 to ~260) | the "Removed in 3.0" list of `docs/migration-2.0.md` | 3.0.0 |
| `commands/` deleted; `agents/README.md` and `commands/README.md` removed | path table rows in `docs/migration-2.0.md`; content in the plugin READMEs | never (git history is the record) |

`docs/migration-2.0.md` is created by WS1 as the skeleton Requirement 14 defines. The rows WS1 itself adds are: one path row per moved component (28 moves plus the three deleted READMEs); the seven install rows; the seven version rows; and the three 3.0 rows above. Later workstreams append their own rows to the same file - WS2 the `6hats` supersession row ([../naming/spec.md](../naming/spec.md)), WS5 the `docs/superpowers/` and `.github/claude-review-instructions.md` rows ([../knowledge/spec.md](../knowledge/spec.md)), WS7 the versioning rows ([../distribution/spec.md](../distribution/spec.md)).

The R20 migration notice itself is a `SessionStart` hook and is WS6's deliverable ([../guardrails/spec.md](../guardrails/spec.md)), not WS1's; WS1 ships the README block and the guide the notice links to.

## Rollback

PR1 lands as a single squashed commit on `main`, so the revert path is `git revert <sha>` followed by the same required checks. Because PR1 is a pure move plus consumer update with zero floor edits (D8), the inverse is clean: `plugins/` disappears, `skills/`, `commands/` and `agents/` return with their READMEs, and the root `plugin.json` and single-entry `marketplace.json` return. Nothing in PR1 writes state outside the repository except the marketplace cache of whoever ran Verification gate 4, which `claude plugin marketplace remove` clears.

Two things a revert cannot undo, so both are sequenced after the point of no return:

- **The `v1.57.0` tag and its GitHub release are immutable once created.** The tag therefore rides the last commit of PR1 and is cut **only after the post-merge self-test is green** - that is, after `tests.yml`'s `push: branches: [main]` run passes under all four job names, `claude plugin validate .` passes with zero warnings on the merged tree, and Verification gate 7's `/assess` self-run reports the instruction-file layer Present with no new orphan and no phantom seam. A revert before the tag is cut un-ships nothing.
- **A user who has already run `/plugin update`** holds the 2.0 umbrella in their cache. A revert restores the 1.x layout on `main` but their next `/plugin update` is what returns them; the umbrella exists precisely so that neither state breaks a name.

If the revert is needed after the tag exists, the correction is a forward fix at `1.57.1` rather than a tag deletion: `assess_gate.py:140` skips the regression compare on a major bump but not on a patch, so adopters' frozen gates stay armed across it (D2).

## Sequencing

**PR0 (WS0)** merges first. PR1 depends on the rename-aware floor it lands: without it, moving the four marked files reads as four deletions and the floor fails four times with no bypass ([../floor/spec.md](../floor/spec.md)).

**PR1 (WS1 + WS2)** is this workstream's only PR, in two commits:

1. The 28 `git mv` operations listed in Design, no content edits.
2. Every consumer, the seven manifests, the umbrella symlinks, the marketplace rewrite, the `.gitignore` allowlist, the three README deletions, the R11 assertions, the R3 contract test, `docs/migration-2.0.md`, the README "Upgrading from 1.x" block, and WS2's `6hats` stub and `huddle` `argument-hint`.

PR1 may not touch `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml` or `FLOOR.md`, and may not change any CI job `name:` literal (D7) - the six required contexts on `main` stay satisfied by jobs of unchanged name. The `claude plugin validate` job PR1 adds is a new job with a new name and is not made required.

The PR body records that `.github/workflows/claude-review.yml:35` checks out the default branch, so the bot reviews PR1 against the pre-move instructions.

After merge: the post-merge self-test defined in Rollback, then `git tag v1.57.0` on the merge commit and the release. **WS3, WS5, WS6 and WS7 unblock at that point**; WS4 unblocks on the same merge but starts with its own prerequisite, the 8-case huddle transfer set (D9).
