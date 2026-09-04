# WS9 spec: Evals

## Intent

Brief R9 of the programme recorded in [intent.md](../intent.md), indexed in [README.md](../README.md); parsed into Task Master tag `modernization-evals`.

`intent.md` carries the Problem Statement, Decisions, Deprecation Path, Programme sequence and Success Criteria. The R-briefs it cites by ID live only in the programme PRD, an un-versioned Task Master working document kept outside the repository, so R9 is carried in here as the durable record:

> **R9. Evals**
>
> - Each portable plugin has `evals/<type>-<slug>/{prompt.md, graders/criteria.md, README.md}` - the CLI's own second format, unchanged - with at least three cases drawn from real runs where they exist (assess golden fixtures, the huddle set from R6, skill-forge transfer sets). One format serves three consumers: `ab-equivalence` reads `prompt.md` as the transfer input; `skill-forge` reads `criteria.md` as the fidelity rubric; `claude plugin eval` runs the directory unchanged when enabled.
> - The merge gate is `ab-equivalence` for moves and `skill-forge` for behaviour change - the repo's own tools. `claude plugin eval` is an additive layer: it is early-access, gated per organisation, and not enabled for this org today (Probe 3).
> - `.github/workflows/evals.yml` runs on `workflow_dispatch` only. Its first step runs `claude plugin eval init --bare probe` in a temp dir and exits neutral with "eval unavailable, skipped" on the early-access line. When available it runs `claude plugin eval plugins/<name> --no-publish --max-cost-usd <n>` and treats exit 2 (cost cap hit) as partial, not failed. No `schedule:` trigger (GitHub disables scheduled workflows on public repos after 60 idle days). `--allow-tools Bash` is granted only for cases that need scripts. `CLAUDE_CODE_OAUTH_TOKEN` is the named secret.
> - `CLAUDE.md` states: a change to any `SKILL.md`, agent, or `CLAUDE.md` runs `ab-equivalence` over the owning plugin's `evals/` before the PR is opened; `claude plugin eval` when available.

## Current state

Measured at 0c6d3ad on this checkout. Every number sits next to the command that produced it, with that command's real output.

### No eval directory exists, and no workflow runs the eval command

```
$ git rev-parse --short HEAD
0c6d3ad

$ find . -type d -name evals -not -path './.git/*'
./docs/design/2026-09-modernization/evals

$ rg -n 'plugin eval' .github/ CLAUDE.md; echo "exit=$?"
exit=1
```

The one match is this programme's own spec directory, not a case set.

### The portable set is five skills across four plugins

`scripts/standalone_skill_config.py` is the register of what ships as a standalone ZIP, and therefore of what "portable" names:

```
$ python3 -c "
import re
t = open('scripts/standalone_skill_config.py').read()
t = t[t.index('SKILLS: dict'):]
print(' '.join(re.findall(r'^    \"([a-z0-9-]+)\": \{', t, re.M)))
"
assess huddle skill-forge deslop semantic-compress
```

Under the plugin table in R1 those five skills sit in four plugins: `assess`, `huddle`, `deslop`, and `skill-craft` (which holds `skill-forge`, `semantic-compress` and `ab-equivalence`). `gh-org` and `delivery` carry no portable skill and are therefore out of R9's scope.

### Existing golden fixtures, corpora and answer keys

The assess plugin's committed inputs:

```
$ ls skills/assess/tests/fixtures/golden/
assess-report-baseline.md
decomposition-parity-report.md
run-context-baseline.json

$ find skills/assess/tests/fixtures -mindepth 1 -maxdepth 1 -type d | sort
skills/assess/tests/fixtures/golden
skills/assess/tests/fixtures/golden-doc-repo
skills/assess/tests/fixtures/golden-svg-repo
skills/assess/tests/fixtures/hollow_test_repo
skills/assess/tests/fixtures/honest_test_repo
skills/assess/tests/fixtures/lean_with_skills
skills/assess/tests/fixtures/maven_project
skills/assess/tests/fixtures/structure_drift
```

The skill-craft plugin's committed inputs, each with an answer key beside it:

```
$ find skills/skill-forge/tests/fixtures -type f | sort
skills/skill-forge/tests/fixtures/flawed-instruction-file/check_counts.py
skills/skill-forge/tests/fixtures/flawed-instruction-file/CLAUDE.md
skills/skill-forge/tests/fixtures/flawed-instruction-file/DEFECTS.md
skills/skill-forge/tests/fixtures/flawed-sample-skill/DEFECTS.md
skills/skill-forge/tests/fixtures/flawed-sample-skill/SKILL.md

$ grep -c '^| "' skills/semantic-compress/references/directive-clarity-rewrites.md
13
```

The 13 table rows are recorded latent-original / directive-rewrite pairs drawn from this repo's own instruction text.

The only forge corpus in the tree belongs to `marathon`, which is in `delivery` and not portable:

```
$ rg -c '^### ' skills/marathon/forge/corpus.md
5

$ rg -o '^### [a-z]+-[0-9]+' skills/marathon/forge/corpus.md
### happy-1
### edge-1
### adv-1
### comp-1
### crash-1
```

`deslop` carries no fixture, no corpus and no recorded run:

```
$ find skills/deslop -type f | sort
skills/deslop/references/full-checklist.md
skills/deslop/SKILL.md
```

### Workflows, triggers and secrets

```
$ ls .github/workflows/
assess-gate.yml
build-standalone-skills.yml
claude-review.yml
floor.yml
pr-lint.yml
tests.yml

$ rg -n 'schedule:' .github/workflows/; echo "exit=$?"
exit=1

$ rg -no 'secrets\.[A-Z_]+' .github/workflows/ | sort -u
.github/workflows/claude-review.yml:48:secrets.CLAUDE_CODE_OAUTH_TOKEN
.github/workflows/floor.yml:105:secrets.FLOOR_ANCHOR_TOKEN
.github/workflows/pr-lint.yml:29:secrets.GITHUB_TOKEN
```

`CLAUDE_CODE_OAUTH_TOKEN` is already configured and already used by `claude-review.yml`, so WS9 adds no new secret. No workflow carries a `schedule:` trigger today; `build-standalone-skills.yml` is the one workflow already on `workflow_dispatch`.

### What the contract test already asserts about any new markdown

`tests/test_plugin_contract.py:83-99` defines `all_authored_markdown()` as every `*.md` in the tree except `.git`, `dist`, `node_modules` and any path containing `fixtures`. Two tests are parametrized over it, `test_no_leaked_tool_envelope_tags` and `test_no_conflict_markers`. Case files under `plugins/<x>/evals/` fall inside that set and outside `shipped_md()`, which is skill and command files only, so link resolution and the placeholder scan do not reach them.

### Probe 3

Probe 3, recorded in [probes.md](../probes.md), settles both the gate and the format. Its gate line, printed by `claude plugin eval init --bare sample` at exit 1:

> `` `plugin eval` is currently in early access ``

Its conclusion is that `evals.yml` must report neutral, because the feature is early-access-gated for this account, and that `--help` exiting 0 is presence rather than entitlement. On the format it establishes exactly one line of `--help`:

> Run eval cases (`<eval dir>/**/case.yaml or prompt.md + graders/*.md`; the eval dir is `evals/` unless `--eval-dir` or the manifest says otherwise)

Probe 3 marks two things as house convention rather than runner requirement, because the gate stopped `eval init --bare` from writing a template: the `evals/<type>-<slug>/` directory naming, which appears nowhere in the help text, and the filename `criteria.md`, where the help text says `graders/*.md`. This spec respects that distinction throughout.

Two further facts the same `--help` output fixes and this design depends on: `--max-cost-usd` aborts and reports partial results at exit 2, and `--allow-tools` is the operator grant for gated tools including `Bash`.

## Design

### The eval directory format

Every path in a code span from here on is a post-PR1 path, `plugins/<family>/evals/...`, because WS9 runs after WS4 and WS8 and therefore after the move (D1; [intent.md](../intent.md), Programme sequence step 2). This is stated once and not repeated per path.

A case is one directory:

```
plugins/<family>/evals/<type>-<slug>/
  prompt.md            the case input, one file, no frontmatter required
  graders/criteria.md  the rubric: what the run must contain to pass
  README.md            what the case holds the skill to, and where its input came from
```

What each consumer reads:

| Consumer | Reads | Contract it reads under |
|----------|-------|-------------------------|
| `ab-equivalence` | `prompt.md` | Its input contract takes `transfer_set` as an array of cases and runs the runner "on the case input" once per version (`skills/ab-equivalence/SKILL.md`, Input contract and Mechanism). The case input is the `prompt.md` body. |
| `skill-forge` | `graders/criteria.md` | The rubric the Fidelity lens scores a run against. Where a target already carries a `forge/corpus.md` sidecar, skill-forge reads that instead; see the rejected alternative below. |
| `claude plugin eval` | the directory | `prompt.md` plus `graders/*.md`, at any depth below `evals/`, per the `--help` line quoted in Current state. |

The runner requires: the eval dir is `evals/`, a case is `case.yaml` or `prompt.md` beside `graders/*.md`, and cases are found at any depth. Everything else in the layout above is house convention this repo may change without breaking the runner: the `<type>-<slug>` directory name, the grader filename `criteria.md`, and the per-case `README.md`. `<type>` is drawn from the four case types in `skills/skill-forge/references/test-taxonomy.md` (happy, edge, adversarial, composition), which is what makes the same directory legible to a forge run.

`evals/` sits at the plugin root, outside every skill's `source_dir`, so `scripts/transform_skill.py` does not copy it and no standalone ZIP grows by a byte. The `.gitignore` allowlist needs no WS9 entry either: it is a top-level allowlist (`/*` then `!/skills/`, `!/docs/` and the rest), and WS1 adds `!/plugins/` under R11, which covers every path below it.

Rejected: relocating the existing `forge/corpus.md` sidecars into `evals/`. `test-taxonomy.md` fixes the corpus location as a sidecar beside the document being forged, and `skills/skill-forge/references/forge-report-template.md` records that location so a later re-forge can reload it. `marathon`'s corpus belongs to `delivery`, which is not portable and gets no `evals/` set at all; `assess-pr`'s corpus is authored by WS8 at `plugins/assess/skills/assess-pr/forge/corpus.md` ([behaviour/spec.md](../behaviour/spec.md), Candidate B). Moving either mid-programme would break a lookup for no gain. The two artefacts coexist: `evals/` is the plugin-level set a runner consumes, `forge/corpus.md` is the per-document accumulating record of one skill's known failure modes.

### The cases, per portable plugin

Two of the four sets already have an owner. WS4 authors them ahead of every split ([disclosure/spec.md](../disclosure/spec.md), Requirement 1 and Sequencing PR-A), so WS9 consumes them rather than re-authoring:

| Plugin | Cases | Where they come from | Authored by |
|--------|-------|----------------------|-------------|
| `huddle` | 5 | The script-graded half of the 8-case transfer set fixed by D9, which doubles as WS9's huddle evals | WS4 PR-A |
| `assess` | 4 | The four scorer cases over `run-context-baseline.json` and run-contexts across `hollow_test_repo`, `honest_test_repo` and `lean_with_skills` | WS4 PR-A |
| `skill-craft` | 4 | The two answer-keyed forge fixtures and the recorded directive-clarity rewrite pairs measured above | WS9 PR-1 |
| `deslop` | 3 | Authored from the skill's own tell list; no recorded run exists | WS9 PR-1 |

`skill-craft`, drawn from committed inputs that already carry an answer key:

| Case | Input | What `criteria.md` holds it to |
|------|-------|-------------------------------|
| `happy-forge-flawed-sample-skill` | `flawed-sample-skill/SKILL.md` | The five per-lens planted defects at the severities its `DEFECTS.md` records |
| `adversarial-forge-count-surface-trap` | `flawed-instruction-file/CLAUDE.md` | The count-surface finding, ground truth in the same directory's `DEFECTS.md` and `check_counts.py`, already asserted deterministically by `tests/test_skill_forge_instruction_files.py` |
| `edge-forge-severity-calibration` | `flawed-sample-skill/SKILL.md` | The four judgement rows of its `DEFECTS.md`: borderline-LOW, borderline-MED, clean-pass and near-miss, where a finding is over-firing |
| `composition-directive-clarity-rewrite` | The Rule 1 and Rule 2 originals from `directive-clarity-rewrites.md` | Each latent original rewritten to the recorded directive, with the Rule 2 fact kept rather than replaced |

`deslop` is the one departure from R9's "drawn from real runs". The measurement above shows the plugin carries no fixture and no corpus, so there is no run to draw from; its three cases are authored from the numbered tells in `skills/deslop/SKILL.md` and the lettered sections of `references/full-checklist.md`, and this spec states that rather than dressing them as recorded:

| Case | Holds the skill to |
|------|--------------------|
| `happy-puffery-and-rule-of-three` | Tells 1 and 3 caught in a passage that carries both |
| `edge-clean-prose-no-findings` | A passage that trips no tell survives unedited; an edit is over-firing |
| `adversarial-em-dash-and-not-x-but-y` | Tells 4 and 8 removed with the sentence's meaning intact, where the shortest fix changes it |

### `evals.yml`

One workflow, `workflow_dispatch` only, two jobs. The probe job decides availability; the eval job is gated on it and runs one leg per portable plugin.

```yaml
name: Plugin evals

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  probe:
    name: eval availability probe
    runs-on: ubuntu-latest
    outputs:
      available: ${{ steps.gate.outputs.available }}
    steps:
      - uses: actions/checkout@<pinned sha>  # v7.0.0
        with:
          persist-credentials: false
      - name: Install Claude Code
        run: <filled by the WS9 PR; see below>
      - name: Probe the early-access gate
        id: gate
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        run: |
          tmp="$(mktemp -d)"
          set +e
          out="$(cd "$tmp" && claude plugin eval init --bare probe 2>&1)"
          code=$?
          set -e
          printf '%s\n' "$out"
          if printf '%s' "$out" | grep -qF 'plugin eval` is currently in early access'; then
            echo "available=false" >> "$GITHUB_OUTPUT"
            echo "::notice::eval unavailable, skipped"
            echo 'eval unavailable, skipped (early access)' >> "$GITHUB_STEP_SUMMARY"
            exit 0
          fi
          [ "$code" -eq 0 ] || exit "$code"
          echo "available=true" >> "$GITHUB_OUTPUT"

  eval:
    name: plugin eval (${{ matrix.plugin }})
    needs: probe
    if: needs.probe.outputs.available == 'true'
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - { plugin: assess,      allow_tools: Bash }
          - { plugin: huddle,      allow_tools: "" }
          - { plugin: deslop,      allow_tools: "" }
          - { plugin: skill-craft, allow_tools: "" }
    steps:
      - uses: actions/checkout@<pinned sha>  # v7.0.0
        with:
          persist-credentials: false
      - name: Install Claude Code
        run: <same line as the probe job>
      - name: Run the eval suite
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          PLUGIN: ${{ matrix.plugin }}
          ALLOW_TOOLS: ${{ matrix.allow_tools }}
        run: |
          args=("plugins/$PLUGIN" --no-publish --max-cost-usd 5)
          [ -n "$ALLOW_TOOLS" ] && args+=(--allow-tools "$ALLOW_TOOLS")
          set +e
          claude plugin eval "${args[@]}"
          code=$?
          set -e
          case "$code" in
            0) ;;
            2) echo "::notice::cost ceiling hit for $PLUGIN, partial results" ;;
            *) exit "$code" ;;
          esac
```

Four points the sketch encodes.

**Neutral is a skipped job, not an exit code.** A `run:` step's exit code resolves to success or failure and nothing else, so "neutral" is expressed as a job GitHub Actions skips: the `eval` job's `if:` reads `needs.probe.outputs.available`, and a false job-level `if:` produces a skipped job (GitHub Actions documentation, "Workflow syntax for GitHub Actions", `jobs.<job_id>.if`). The probe job stays green and prints `eval unavailable, skipped` as a `::notice` and a step-summary line, so the run is legible without a red check. This is also why no `evals.yml` job is added to branch protection: [floor/spec.md](../floor/spec.md) records that branch protection reads a skipped required context as satisfied, which would make a gated eval indistinguishable from a passing one.

**Exit 2 is partial, exit 1 is a real failure.** `--max-cost-usd` aborts at exit 2 with partial results (Probe 3's `--help`), so the `case` maps 2 to a notice and leaves the job green. `--threshold` keeps its documented default of 1.0, so a case scoring below threshold exits 1 and the job goes red - which is the point of running the suite at all.

**`--allow-tools Bash` is scoped to `assess` alone.** The flag is a run-level operator grant, not a per-case one, so scoping means splitting the run. The matrix already splits it per plugin, and only the assess family drives an agent that shells out: of the five SKILL.md files carrying a shell invocation at 0c6d3ad (`assess`, `assess-findings`, `assess-pr`, `ghsync`, `ghreport`), only the first three sit in a portable plugin. The other three legs run with no grant, so a case that tries to shell out fails rather than silently succeeding.

**The install line is not fixed here.** Probe 3 ran `claude` on this machine, not in Actions, so no recorded run establishes the CI install command. The WS9 PR fills both `Install Claude Code` steps from the installation page current at the time and pastes the first `workflow_dispatch` run's `claude --version` output into the PR body, which is the same evidence Probe 3 opens with.

Rejected: a `schedule:` trigger. GitHub disables a scheduled workflow in a public repository after 60 days without repository activity (GitHub Actions documentation, "Events that trigger workflows", `schedule`), so a scheduled eval run would go quiet exactly when the repository is quiet, which is when a silent regression is least likely to be noticed by any other means. The suite is a dispatch a maintainer fires; the merge gate is `ab-equivalence` and `skill-forge` per R9, and those run before the PR is opened.

Rejected: keying availability off `claude plugin eval --help` exiting 0. Probe 3 states plainly that `--help` exits 0 while the feature is gated, so presence is not entitlement and a workflow keyed on it would run a gated command and fail.

Rejected: `pull_request` as a trigger. It would put a paid, network-bound, model-graded run on every PR while the feature is gated for this account, and R9 makes `claude plugin eval` an additive layer rather than a merge gate.

### The `CLAUDE.md` rule

The rule R9 asks for, as one bullet under `## Invariants`:

```
- A change to any `SKILL.md`, agent file, or `CLAUDE.md` runs `ab-equivalence` over the owning plugin's `evals/` before the PR is opened, and the per-case verdict goes in the PR body. Run `claude plugin eval plugins/<name>` too, once the feature is enabled for this account.
```

That bullet is 275 bytes, measured against the copy in this file:

```
$ sed -n '/^- A change to any/p' docs/design/2026-09-modernization/evals/spec.md | wc -c
     275
```

`CLAUDE.md` belongs to WS5 under R7, which sets its 8,192-byte cap, its `test_claude_md_expanded_under_8kb` contract test, and a design target of 7,700 bytes with a recorded 895 bytes of headroom under that target ([knowledge/spec.md](../knowledge/spec.md), "The byte budget for `CLAUDE.md`"). The bullet spends 275 of those 895 and adds no heading.

WS9 lands the bullet in its own PR rather than asking WS5 to carry it. WS5's four PRs run in parallel with WS3 and complete long before WS4 and WS8, so a rule landed there would name `evals/` directories that do not exist yet, and WS5's keep-and-move table carries no row for it. Rejected: adding the rule to WS5's PR-A (states a rule against absent directories); rejected: a new `## Evals` heading (a heading plus body costs more of WS5's headroom than a bullet, for one sentence).

## Requirements

Each requirement is verifiable by a command, a test, or a recorded verdict. Paths are post-PR1 (`plugins/<family>/...`), as stated once in Design.

1. `plugins/deslop/evals/` holds the three case directories named in Design, and `plugins/skill-craft/evals/` holds the four, each with `prompt.md`, `graders/criteria.md` and `README.md`. Verified by `find plugins/deslop/evals plugins/skill-craft/evals -name prompt.md | wc -l` printing 7 and by the same `find` over `graders/criteria.md` printing 7.
2. Every portable plugin has at least three cases: `assess` 4, `huddle` 5, `skill-craft` 4, `deslop` 3. Verified by `for p in assess huddle deslop skill-craft; do printf '%s %s\n' "$p" "$(find plugins/$p/evals -name prompt.md | wc -l)"; done`.
3. `gh-org` and `delivery` carry no `evals/` directory, matching the portable set measured in Current state. Verified by `find plugins/gh-org plugins/delivery -type d -name evals` returning nothing.
4. Each `skill-craft` case's `README.md` names its committed input by path, and each input exists. Verified by resolving every path named in the four README files against the tree.
5. Each `deslop` case's `README.md` states that the case is authored rather than drawn from a recorded run, and names the tell numbers from `plugins/deslop/skills/deslop/SKILL.md` it holds the skill to. Verified by reading the three files.
6. No case file carries a leaked tool-envelope tag or a git conflict marker. A case whose input needs to mention a conflict marker references it as inline code, the rule `tests/test_plugin_contract.py` states in its own `CONFLICT_MARKER_RE` comment. Verified by `test_no_leaked_tool_envelope_tags` and `test_no_conflict_markers`, which are already parametrized over these paths (Current state).
7. `.github/workflows/evals.yml` declares `on: workflow_dispatch` and no other trigger. Verified by `rg -n 'schedule:|pull_request:|push:' .github/workflows/evals.yml` returning nothing.
8. The probe job's grep matches the Probe 3 gate line and sets `available=false`, and does not match a success output. Verified by `scripts/tests/test_evals_workflow.py` against the fixture named in Verification.
9. The eval job is gated on the probe output, so a gated account produces one green probe job and four skipped eval jobs. Verified by the first `workflow_dispatch` run, its job conclusions pasted into the PR body.
10. The eval command carries `--no-publish` and `--max-cost-usd`, and exit 2 is reported as a notice while any other non-zero exit fails the job. Verified by reading the `case` block and by `scripts/tests/test_evals_workflow.py`.
11. `--allow-tools Bash` appears on the `assess` matrix leg and on no other. Verified by `rg -n 'allow_tools' .github/workflows/evals.yml`.
12. `CLAUDE_CODE_OAUTH_TOKEN` is the only secret `evals.yml` names. Verified by `rg -no 'secrets\.[A-Z_]+' .github/workflows/evals.yml | sort -u`.
13. No `evals.yml` job name is added to branch protection, so the required-check set is unchanged. Verified by the repository's branch-protection contexts before and after, pasted into PR-2's body.
14. `CLAUDE.md` carries the rule bullet, and the file stays under the 8,192-byte cap with `@` imports expanded. Verified by `rg -n "ab-equivalence.*evals/" CLAUDE.md` and by `tests/test_plugin_contract.py::test_claude_md_expanded_under_8kb`, the test WS5 ships.
15. Each PR bumps the version of every plugin it touches, and the meta-plugin, per R10. PR-2 and PR-3 touch no plugin component and need no bump. Verified by the same-PR bump contract test WS7 ships.
16. The standalone ZIPs are byte-identical in file list before and after PR-1, because `evals/` sits outside every skill's `source_dir`. Verified by `scripts/tests/test_integration.py` and by comparing the built ZIP manifests across the PR.

## Verification

The merge gate stays `ab-equivalence` for moves and `skill-forge` for intended change (R9). `claude plugin eval` is the additive layer and gates nothing while it is gated for this account.

| What | Set or fixture | Where it comes from |
|------|----------------|---------------------|
| `huddle` evals | The five script-graded cases of the 8-case transfer set | WS4 PR-A, enumerated in [disclosure/spec.md](../disclosure/spec.md) |
| `assess` evals | The four scorer cases over `run-context-baseline.json`, `hollow_test_repo`, `honest_test_repo` and `lean_with_skills` | WS4 PR-A, same table |
| `skill-craft` evals | `flawed-sample-skill/{SKILL.md,DEFECTS.md}`, `flawed-instruction-file/{CLAUDE.md,DEFECTS.md,check_counts.py}`, and the 13 rewrite rows of `directive-clarity-rewrites.md` | Committed at 0c6d3ad, measured in Current state |
| `deslop` evals | The numbered tells of `deslop/SKILL.md` and sections A to F of `references/full-checklist.md` | Committed at 0c6d3ad, authored into cases by PR-1 |
| The neutral-exit path | `scripts/tests/fixtures/eval_gate_early_access.txt`, holding the Probe 3 stdout verbatim, and a sibling `eval_gate_available.txt` holding a non-gated `init` output | PR-2 |

`scripts/tests/test_evals_workflow.py` is the deterministic half. It parses `.github/workflows/evals.yml`, extracts the probe step's grep pattern, and asserts three things: the pattern matches `eval_gate_early_access.txt` and not `eval_gate_available.txt`; the `eval` job's `if:` reads the probe job's `available` output; and the run step maps exit 2 to a notice while leaving every other non-zero exit fatal. It runs under the existing required `scripts/ pytest` context, which has no path filter (`.github/workflows/tests.yml` triggers on `pull_request: branches: [main]` with no `paths:`), so it runs on every PR that touches the workflow.

The first `workflow_dispatch` run is the live half: one green `eval availability probe` job carrying the `eval unavailable, skipped` notice, and four skipped `plugin eval` jobs. Those conclusions go in PR-2's body, the same evidence shape [floor/spec.md](../floor/spec.md) uses for its skipped and refused sign-off observations.

`tests/test_plugin_contract.py` covers the case files themselves through `test_no_leaked_tool_envelope_tags` and `test_no_conflict_markers`, and `CLAUDE.md` through the WS5 size test.

## Breadcrumbs

None. WS9 leaves no shim, no alias, and no `remove-in` marker, and adds no row to `docs/migration-2.0.md`. An `evals/` directory is a live test asset, not a compatibility layer; `evals.yml` is a new workflow with no predecessor to point at; the `CLAUDE.md` bullet is a rule, not a pointer to a moved section.

## Rollback

Each PR reverts independently with `git revert`, and none of them can leave `main` red.

- **PR-1** deletes `plugins/deslop/evals/` and `plugins/skill-craft/evals/` and the two version bumps in one commit. Nothing reads those directories at merge time: `evals.yml` does not exist yet under this ordering, and no test is parametrized over the case set by name. The contract tests that do reach the files simply stop being parametrized over them.
- **PR-2** deletes `evals.yml`, `scripts/tests/test_evals_workflow.py` and the two fixtures together. Because the workflow is `workflow_dispatch` only, there is nothing to disarm: no run can have been triggered by a schedule, a push or a pull request, so a revert cannot leave a queued or recurring run behind. No `evals.yml` job is a required context (Requirement 13), so removing the workflow changes no branch-protection contract and no PR is left waiting on an absent check.
- **PR-3** removes one bullet from `CLAUDE.md`, which moves the expanded size down, away from the cap. `test_claude_md_expanded_under_8kb` asserts a ceiling, so a smaller file passes.

No PR in this workstream touches `FLOOR.md`, a floor-marked file, `floor.yml`, `action.yml`, or a CI job `name:`. Under the role-based classification WS0 introduces, `.github/workflows/evals.yml` is none of the four protected roles - it is not a marked component, not gate code, not a canary fixture, and not floor core, which among workflows names `floor.yml` alone ([floor/spec.md](../floor/spec.md)) - so neither landing it nor reverting it involves a sign-off.

## Sequencing

WS9 starts after WS4 and WS8 have merged. WS4 authors the `huddle` and `assess` case sets in its PR-A and finishes with PR-D ([disclosure/spec.md](../disclosure/spec.md)); WS8 finishes with its Candidate C PR ([behaviour/spec.md](../behaviour/spec.md)), and WS9's suite has to run against the promoted bodies rather than the pre-forge ones. PR-3 additionally waits on WS5's PR-A, which is the PR that shrinks `CLAUDE.md` to its post-R7 shape.

| PR | Content | May not touch |
|----|---------|---------------|
| PR-1 | `plugins/deslop/evals/` (3 cases), `plugins/skill-craft/evals/` (4 cases), the `deslop`, `skill-craft` and meta-plugin version bumps | `.github/`, `CLAUDE.md`, any `SKILL.md` or agent body, the WS4-authored `plugins/huddle/evals/` and `plugins/assess/evals/` sets, either `forge/corpus.md` sidecar |
| PR-2 | `.github/workflows/evals.yml`, `scripts/tests/test_evals_workflow.py`, `scripts/tests/fixtures/eval_gate_early_access.txt`, `scripts/tests/fixtures/eval_gate_available.txt` | `plugins/`, `CLAUDE.md`, `.github/workflows/floor.yml`, `FLOOR.md`, any existing job `name:` |
| PR-3 | The `CLAUDE.md` rule bullet | Everything else |

PR-1 and PR-2 share no file and can run in parallel. PR-3 merges last: it states a rule about `evals/` directories and a workflow, so both should exist before `CLAUDE.md` claims they do.
