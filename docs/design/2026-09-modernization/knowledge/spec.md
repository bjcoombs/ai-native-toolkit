# WS5 spec: Knowledge files

## Intent

Satisfies briefs R7 (CLAUDE.md under a page), R8 (review policy file), R13 (design history under the playbook's vocabulary) and R15 (per-plugin CHANGELOG) of the programme recorded in [intent.md](../intent.md); tag `modernization-knowledge`, indexed in [README.md](../README.md).

## Current state

Every number below was measured at `44bddbf` on this checkout, with the command that produced it. The commands are re-runnable against `main` at that commit.

### `CLAUDE.md` size, raw and expanded

R7 caps the file at 8 KB **loaded, with `@` imports expanded**, because a line count is gameable by imports. This file currently declares no imports, so expanded equals raw.

```
$ git rev-parse HEAD
44bddbfa68d70685be3d6691bc17445ab98386ed

$ wc -c -l CLAUDE.md
     223   23581 CLAUDE.md

$ grep -cE '(^|[[:space:]])@[A-Za-z0-9./~_-]+' CLAUDE.md
0
```

The expander used for the contract number, run from the repo root:

```
$ python3 - <<'PY'
import pathlib, re
def expand(p, seen=None):
    seen = seen if seen is not None else set()
    out = []
    for line in pathlib.Path(p).read_text().splitlines(keepends=True):
        m = re.match(r"^@([\w./~-]+)\s*$", line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(expand(m.group(1), seen))
        else:
            out.append(line)
    return "".join(out)
t = expand("CLAUDE.md")
print(f"{len(t.encode()):d} bytes, {t.count(chr(10)):d} lines (expanded)")
PY
23581 bytes, 223 lines (expanded)
```

23,581 bytes expanded against a 8,192-byte cap: 15,389 bytes over.

### `CLAUDE.md` per-section sizes

```
$ awk '/^#{2,3} /{if(name)printf "%6d bytes %4d lines  %s\n", bytes, lines, name; name=$0; lines=0; bytes=0} {lines++; bytes+=length($0)+1} END{printf "%6d bytes %4d lines  %s\n", bytes, lines, name}' CLAUDE.md
   878 bytes    6 lines  ## Scope of this repo
  3357 bytes   18 lines  ## North star
   695 bytes   12 lines  ## Versioning
    21 bytes    2 lines  ## File conventions
   386 bytes    9 lines  ### `skills/<name>/SKILL.md`
   271 bytes    9 lines  ### `agents/<name>.md`
  1073 bytes   11 lines  ### `commands/<name>.md`
   384 bytes    6 lines  ## Invariants
  2126 bytes    9 lines  ## CI
   322 bytes    4 lines  ## Marathon Configuration
   268 bytes    7 lines  ### Branch and Merge
   407 bytes    8 lines  ### Bot Reviewers
  2038 bytes    8 lines  ### CI Patterns
   253 bytes    7 lines  ### GitHub Issues (for `/issues`)
   294 bytes    4 lines  ### Retrospective
  2313 bytes   25 lines  ### Release after a marathon
   404 bytes    4 lines  ## Testing a branch before merging
  4038 bytes   31 lines  ## Standalone skill pipeline
  3412 bytes   31 lines  ## /assess architecture
   280 bytes    5 lines  ## What this repo doesn't have (and that's fine)
   148 bytes    3 lines  ## Compatibility

$ sed -n '1,4p' CLAUDE.md | wc -c
     213
```

The single longest line in `## CI` is the bullet R7 names for trimming:

```
$ awk 'NR>=78 && NR<=86 {printf "%d: %d chars\n", NR, length($0)}' CLAUDE.md
78: 5 chars
79: 0 chars
80: 290 chars
81: 1011 chars
82: 453 chars
83: 159 chars
84: 0 chars
85: 199 chars
86: 0 chars
```

### The four runtime readers of the Marathon Configuration heading

```
$ rg -n "CLAUDE.md.{0,40}Marathon Configuration|Marathon Configuration.{0,40}CLAUDE.md" \
    commands/tm.md commands/issues.md skills/marathon/SKILL.md skills/pr-review-merge/SKILL.md | sort | cut -c1-118
commands/issues.md:17:Read the repo's CLAUDE.md `## Marathon Configuration` (GitHub Issues subsection) for label
commands/tm.md:46:Read the repo's CLAUDE.md for a `## Marathon Configuration` section. This provides project-specific 
commands/tm.md:59:No Marathon Configuration found in this project's CLAUDE.md.
commands/tm.md:61:For best results, add a ## Marathon Configuration section to your project's CLAUDE.md.
skills/marathon/SKILL.md:48:Read the repo's CLAUDE.md for a `## Marathon Configuration` section. This provides project
skills/marathon/SKILL.md:61:No Marathon Configuration found in this project's CLAUDE.md.
skills/marathon/SKILL.md:63:For best results, add a ## Marathon Configuration section to your project's CLAUDE.md.
skills/pr-review-merge/SKILL.md:124:- Unresolved bot threads - **Check local code first** at the referenced path:line.
skills/pr-review-merge/SKILL.md:34:Follow bot reviewer rules from the project's CLAUDE.md Marathon Configuration. Gene
```

The four sites R7 names ([commands/tm.md](../../../../commands/tm.md) line 46, [skills/marathon/SKILL.md](../../../../skills/marathon/SKILL.md) line 48, [skills/pr-review-merge/SKILL.md](../../../../skills/pr-review-merge/SKILL.md) lines 34 and 124, [commands/issues.md](../../../../commands/issues.md) line 17) are unmoved at `44bddbf`. `tm.md:46` and `marathon/SKILL.md:48` extract the same six settings by name: `$BASE_BRANCH`, `$REQUIRED_APPROVALS`, `$MARKDOWN_APPROVALS`, `$RETRO_LOG`, bot reviewer rules, CI patterns. `issues.md:17` additionally reads the GitHub Issues subsection labels. Adopters carry the same heading in their own `CLAUDE.md`, so neither the heading text nor the six setting names may change.

### `.github/claude-review-instructions.md` and what the bot depends on

```
$ wc -c -l .github/claude-review-instructions.md
     237   10836 .github/claude-review-instructions.md

$ grep -n 'claude-review-instructions' .github/workflows/claude-review.yml
50:            Read `.github/claude-review-instructions.md` for your complete review

$ grep -n 'ref:' .github/workflows/claude-review.yml
35:          ref: ${{ github.event.repository.default_branch }}
```

The workflow checks out the default branch, not the PR head, so a PR that introduces a new policy file is still reviewed by whatever the default branch holds.

Elements the workflow and the bot depend on, from the file's own headings:

```
$ grep -n '^#\{1,3\} ' .github/claude-review-instructions.md
1:# Claude Code Review Instructions
7:## Project
19:## Your role
32:## Review Focus (what to look out for)
34:### 1. Plugin contract invariants
48:### 2. Versioning discipline
56:### 3. Standalone-skill markers (high-value, easy to miss)
66:### 4. Deterministic-core boundary (`/assess`)
76:### 5. Python quality gates
82:### 6. Truth-pressure / honest docs
90:### 7. Secrets / public-repo hygiene (always check)
96:### 8. House conventions
102:### 9. Scope / blast radius
109:## Read before you review
116:## CI status
125:## Bot comment gate (CodeRabbit)
146:## Review outcomes (three states)
158:## Feedback principles
166:## Comment management
175:## Claude Code Review
179:### Summary
182:### Findings
185:### Bot Review Notes
188:### Questions for the Author
193:# create
195:# update
199:## Inline comments
216:## Resolving your previous threads
232:# for each addressed thread id:
```

The six contracts the workflow prompt or the bot's own output depends on: the `{REPO}` / `{PR_NUMBER}` / `{HEAD_SHA}` / `{REPO_OWNER}` / `{REPO_NAME}` / `{CURRENT_DATE}` placeholder substitution declared at line 4 and supplied by the workflow prompt; the single-summary-comment upsert keyed on `claude[bot]` plus the literal `## Claude Code Review` heading (line 171); the never-reply-in-a-CodeRabbit-thread rule (line 142); self-thread resolution keyed on `claude[bot]` (line 230); the three outcome states (line 146) and the required-check names (line 122).

The path is also a scanned instruction file in the deterministic core:

```
$ sed -n '108,121p' skills/assess/scripts/assess_core.py
INSTRUCTION_FILE_PATHS = [
    # Canonical repo-root locations
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    # Tool-specific locations under .github/
    ".github/copilot-instructions.md",
    ".github/claude-instructions.md",
    ".github/claude-review-instructions.md",
    # docs/ subdirectory variants used by some projects
    "docs/CLAUDE.md",
    "docs/AGENTS.md",
]
```

`REVIEW.md` is not in that list. A move that leaves a three-line pointer behind therefore replaces an A-graded scanned file with a stub and hides the real policy from the layer-0 scan.

### `docs/superpowers/` file list

```
$ find docs/superpowers -type f | sort
docs/superpowers/plans/2026-05-22-assess-deterministic-wiki.md
docs/superpowers/plans/2026-05-22-assess-v1.5-real-use-fixes.md
docs/superpowers/plans/2026-05-23-standalone-skill-pipeline.md
docs/superpowers/plans/2026-05-27-assess-truth-pressure-signals.md
docs/superpowers/plans/2026-05-27-huddle-broadcast-per-recipient.md
docs/superpowers/plans/2026-05-27-huddle-cli-team-mode-regression.md
docs/superpowers/plans/2026-05-28-assess-dismiss-false-positives.md
docs/superpowers/plans/2026-05-29-assess-keyhole-readiness.md
docs/superpowers/plans/2026-05-29-assess-write-side-truth-pressure.md
docs/superpowers/plans/2026-05-29-issues-marathon-shared-skills.md
docs/superpowers/plans/2026-05-31-assess-dogfooded-analysis.md
docs/superpowers/plans/2026-05-31-assess-dogfooded.md
docs/superpowers/plans/2026-06-02-skill-forge.md
docs/superpowers/README.md
docs/superpowers/specs/2026-05-29-issues-marathon-shared-skills-design.md
docs/superpowers/specs/2026-06-02-skill-forge-design.md
docs/superpowers/specs/2026-06-03-instruction-optimizer-directive-clarity-design.md
docs/superpowers/specs/2026-06-03-semantic-compress-distillation-design.md
docs/superpowers/specs/2026-06-03-skill-forge-extraction-verification.md
docs/superpowers/specs/2026-06-03-skill-forge-hardening-and-ab-extraction-design.md
docs/superpowers/specs/2026-06-04-assess-feedback-seams-and-archtest-design.md
docs/superpowers/specs/2026-06-24-ghreport-org-state-design.md

$ find docs/superpowers -type f -name '*.md' | sed 's|/[^/]*$||' | sort | uniq -c
   1 docs/superpowers
  13 docs/superpowers/plans
   8 docs/superpowers/specs
```

22 files: one `README.md` index (3,325 bytes), 13 plans, 8 specs. Inbound references from outside the directory:

```
$ rg -n 'docs/superpowers|\./superpowers' --glob '!docs/superpowers/**' --glob '!.assess/**' --glob '!docs/design/**' -l . | sort
./docs/index.md
./skills/assess/tests/fixtures/golden/assess-report-baseline.md
./skills/assess/tests/fixtures/golden/run-context-baseline.json
./skills/skill-forge/SKILL.md
```

[skills/skill-forge/SKILL.md](../../../../skills/skill-forge/SKILL.md) line 159 links `docs/superpowers/specs/2026-06-02-skill-forge-design.md` and is covered by `test_internal_links_resolve`; [docs/index.md](../../../index.md) line 72 links `./superpowers/README.md` and is not (that test parametrizes over `shipped_md()`, which is SKILL.md plus `commands/*.md`). The two golden fixtures are static captures of a past run and are not recomputed from the tree.

### What does not exist yet

```
$ ls CHANGELOG.md REVIEW.md docs/migration-2.0.md docs/design/TEMPLATE scripts/README.md skills/marathon/references
ls: CHANGELOG.md: No such file or directory
ls: docs/design/TEMPLATE: No such file or directory
ls: docs/migration-2.0.md: No such file or directory
ls: REVIEW.md: No such file or directory
ls: scripts/README.md: No such file or directory
ls: skills/marathon/references: No such file or directory
```

`plugins/` does not exist either; PR1 (WS1) creates it. Every destination path below is stated in its post-PR1 form.

## Design

WS5 lands after PR1, so the measurements above are a pre-PR1 baseline. Two of them move under PR1: the `### commands/<name>.md` conventions subsection (1,073 bytes) goes with `commands/` under R2, and the four reader sites acquire `plugins/<family>/` prefixes. Every WS5 PR re-runs the measurement commands in this section against its own base and states the result in its body; a number that has drifted is re-derived, not carried forward.

### The byte budget for `CLAUDE.md` (R7)

R7's keep/move list does not close the 8 KB cap on its own. Kept blocks, summed at their `44bddbf` sizes and excluding the `commands/` subsection PR1 removes:

| Kept block | Bytes |
|---|---|
| Header (lines 1-4) | 213 |
| `## Scope of this repo` | 878 |
| `## Versioning` | 695 |
| `## File conventions` intro | 21 |
| `### skills/<name>/SKILL.md` | 386 |
| `### agents/<name>.md` | 271 |
| `## Invariants` | 384 |
| `## CI` | 2,126 |
| `## Marathon Configuration` intro | 322 |
| `### Branch and Merge` | 268 |
| `### Bot Reviewers` | 407 |
| `### CI Patterns` | 2,038 |
| `### GitHub Issues (for /issues)` | 253 |
| `### Retrospective` | 294 |
| `## Testing a branch before merging` | 404 |
| `## Compatibility` | 148 |
| Kept subtotal | 9,108 |
| Four added pointer lines (three moves plus the Scope absorption) | approx. 440 |
| Total before trimming | approx. 9,548 |

That is 1,356 bytes over the cap with nothing left to move, so the design commits named trims inside kept blocks rather than a further move:

| Trim | From | To | Saved |
|---|---|---|---|
| `## CI` line 81, the 1,011-character bullet | 2,126 | approx. 1,120 | approx. 1,006 |
| `### CI Patterns`, bullets rewritten as fact lines with the prose rationale dropped | 2,038 | approx. 1,300 | approx. 738 |
| `## Marathon Configuration` intro and `### Bot Reviewers` prose | 729 | approx. 520 | approx. 209 |

Trimmed total: approximately 7,595 bytes, against a 8,192-byte cap. The design target is 7,700 bytes so an ordinary later edit does not trip the gate on its first line. The six setting names and the heading text survive every trim; only rationale prose is cut.

Rejected: raising the cap to 12 KB (the cap is the brief's acceptance criterion, and the file is 23,581 bytes today, so the cap is what forces the moves at all). Rejected: moving `### CI Patterns` out of `## Marathon Configuration` into a reference file (`tm.md:46` and `marathon/SKILL.md:48` extract "CI patterns" from inside the section, and adopters copy the section wholesale, so an out-of-file pointer breaks the read for every adopter as well as this repo).

### Keep and move, every `CLAUDE.md` section

| Section | Bytes | Disposition | Destination |
|---|---|---|---|
| Header (lines 1-4) | 213 | Keep | - |
| `## Scope of this repo` | 878 | Keep, plus one absorbed line | - |
| `## North star` | 3,357 | Move | `README.md`, replaced by a three-line pointer |
| `## Versioning` | 695 | Keep | Per-plugin semver wording is R10 and belongs to WS7; WS5 keeps the heading and the bump-in-the-same-PR rule |
| `## File conventions` intro | 21 | Keep | - |
| `### skills/<name>/SKILL.md` | 386 | Keep | - |
| `### agents/<name>.md` | 271 | Keep, gains a `disallowedTools` line | WS3 ships the field; WS5 documents it |
| `### commands/<name>.md` | 1,073 | Removed by PR1 with `commands/` (R2) | - |
| `## Invariants` | 384 | Keep | - |
| `## CI` | 2,126 | Keep, trimmed | - |
| `## Marathon Configuration` intro | 322 | Keep, trimmed | Heading text unchanged |
| `### Branch and Merge` | 268 | Keep | - |
| `### Bot Reviewers` | 407 | Keep, trimmed | - |
| `### CI Patterns` | 2,038 | Keep, trimmed | - |
| `### GitHub Issues (for /issues)` | 253 | Keep | - |
| `### Retrospective` | 294 | Keep | - |
| `### Release after a marathon` | 2,313 | Move | `plugins/delivery/skills/marathon/references/release-after-marathon.md` |
| `## Testing a branch before merging` | 404 | Keep | - |
| `## Standalone skill pipeline` | 4,038 | Move | `scripts/README.md` (new file) |
| `## /assess architecture` | 3,412 | Move | `plugins/assess/README.md` |
| `## What this repo doesn't have (and that's fine)` | 280 | Move | One line inside `## Scope of this repo` |
| `## Compatibility` | 148 | Keep | - |

`release-after-marathon.md` is the only place the `v*` tag Dependabot reads gets cut, so it needs a named owner rather than a deletion; the marathon skill is that owner.

### `REVIEW.md` and the pointer (R8)

`REVIEW.md` at the repo root carries the review policy. `.github/workflows/claude-review.yml` line 50 is re-pointed at it. The five depended-on elements listed in Current state move verbatim: the placeholder contract, the summary-comment upsert key, the CodeRabbit never-reply rule, self-thread resolution, the three outcome states, and the required-check names. `.github/claude-review-instructions.md` becomes a three-line pointer with `remove-in: 3.0.0`.

The layer-0 problem the pointer creates is settled by adding `"REVIEW.md"` to `INSTRUCTION_FILE_PATHS` in `skills/assess/scripts/assess_core.py`, in the same PR that creates the file. That is an additive change to the deterministic core, so it is an `assess` MINOR under D2 and leaves `assess_gate.py`'s regression compare armed. Rejected: leaving `REVIEW.md` unscanned (the layer's evidence would then rest on `CLAUDE.md` alone while an A-graded file degrades to a stub, which is the lying-map failure the layer exists to catch). Rejected: keeping the policy in `.github/` and skipping R8 (the playbook's repo-root `REVIEW.md` is what the brief asks for, and the root position is what makes the policy discoverable to a human reviewer).

Because `claude-review.yml` checks out the default branch, the PR that introduces `REVIEW.md` is reviewed by the old file. The PR body says so.

### `docs/superpowers/` to `docs/design/` (R13)

`docs/superpowers/plans/` and `docs/superpowers/specs/` move unchanged to `docs/design/plans/` and `docs/design/specs/`, beside the `docs/design/2026-09-modernization/` directory this programme already created. New design work uses `docs/design/<yyyy-mm-dd>-<slug>/` with `intent.md`, `spec.md`, `plan.md`; `docs/design/TEMPLATE/` carries that skeleton, and its `spec.md` links to the Task Master PRD as the task-generation source. `docs/superpowers/README.md` stays as a redirect stub with a table mapping all 21 moved files, `remove-in: 3.0.0`.

Rejected: moving the existing files into dated `<yyyy-mm-dd>-<slug>/` directories to match the new convention (21 renames with no reader benefit; R13 says the existing files move unchanged).

### Per-plugin `CHANGELOG.md` (R15)

Each of the seven plugins gets `plugins/<name>/CHANGELOG.md` in Keep a Changelog format, seeded with the section for its first version under D2: `assess` 1.57.0, the other five families and the meta-plugin 2.0.0. Each 2.0.0 entry links `docs/migration-2.0.md` per R18. A root `CHANGELOG.md` carries the `assess` entries and a pointer to the six other files, because Dependabot renders a Changelog section only from the repo root or the action's directory, and the action lives at the repo root under D7. The contract test that requires a new section whenever a version moves is R10 and ships with WS7; WS5 ships the files it will assert against.

Rejected: a single root `CHANGELOG.md` for all seven plugins (Dependabot would render six families' churn into every action consumer's PR).

## Requirements

1. `CLAUDE.md`, with `@` imports expanded, is at most 8,192 bytes, and at most 7,700 bytes at the moment WS5's last PR merges. Verified by the expander command in Current state and by the contract test named in Verification.
2. The literal heading `## Marathon Configuration` is unchanged in `CLAUDE.md`, and the section still names all six settings (`$BASE_BRANCH`, `$REQUIRED_APPROVALS`, `$MARKDOWN_APPROVALS`, `$RETRO_LOG`, bot reviewer rules, CI patterns) plus the four GitHub Issues label settings. Verified by a contract test.
3. The four reader sites resolve their values from the post-WS5 `CLAUDE.md` with no edit to any reader file. Verified by a recorded `/tm` dry read pasted into the PR body, plus requirement 2's test.
4. Each moved section's text is present at its destination and absent from `CLAUDE.md`, with a one-line pointer in `CLAUDE.md` naming the destination. Verified by `rg` for a distinctive sentence of each moved section, run in the PR body.
5. `REVIEW.md` exists at the repo root and carries all six depended-on elements listed in Current state. Verified by a grep of the six literals in the PR body.
6. `.github/workflows/claude-review.yml` reads `REVIEW.md` and no longer reads `.github/claude-review-instructions.md`; the workflow's `ref:` line is unchanged.
7. `.github/claude-review-instructions.md` is a three-line pointer to `REVIEW.md` carrying `remove-in: 3.0.0`.
8. `"REVIEW.md"` is in `INSTRUCTION_FILE_PATHS` in `skills/assess/scripts/assess_core.py`, and `plugins/assess/.claude-plugin/plugin.json` takes a MINOR bump in the same PR.
9. An `/assess` self-run on the post-WS5 tree reports layer 0 at Present, with `REVIEW.md` named in the evidence column. Recorded verdict pasted into the PR body.
10. `docs/design/plans/` and `docs/design/specs/` hold the 13 plans and 8 specs byte-identically; `git diff -M50% --name-status` reports R100 for all 21.
11. `docs/superpowers/README.md` is a redirect stub whose table maps all 21 moved files to their new paths, with `remove-in: 3.0.0`.
12. `docs/design/TEMPLATE/` holds `intent.md`, `spec.md` and `plan.md` skeletons, and `spec.md` names the Task Master PRD as the task-generation source.
13. Every inbound reference in Current state is re-pointed: `skills/skill-forge/SKILL.md` line 159 and `docs/index.md` line 72. The two golden fixtures are static captures and are not edited.
14. Each of the seven plugins has `plugins/<name>/CHANGELOG.md` in Keep a Changelog format with a section for its first version; each 2.0.0 section links `docs/migration-2.0.md`.
15. Root `CHANGELOG.md` exists, carries the `assess` 1.57.0 section, and points at the six family files.
16. Every WS5 PR restates the Current state numbers it depends on, measured against its own base.

## Verification

- `tests/test_plugin_contract.py::test_claude_md_expanded_under_8kb` - new. Expands `@` imports from `CLAUDE.md` with the recursion in Current state and asserts the encoded length is at most 8,192 bytes. Guards requirement 1.
- `tests/test_plugin_contract.py::test_marathon_config_contract` - new. Asserts the literal `## Marathon Configuration` heading is present in `CLAUDE.md` and that all six setting names and the four issue-label names appear under it. Guards requirements 2 and 3, and is the regression guard for an adopter-visible break.
- `tests/test_plugin_contract.py::test_internal_links_resolve` - existing, parametrized over `shipped_md()`. Catches the `skills/skill-forge/SKILL.md` link once `docs/superpowers/specs/` moves. Guards requirement 13 for that file.
- `skills/assess/tests/test_assess_core.py::test_review_md_is_scanned` - new, alongside the existing `.github/claude-review-instructions.md` scan test at line 1002. Writes a synthetic `REVIEW.md` into a fixture repo and asserts it appears in `ctx["instruction_files"]`. Guards requirement 8.
- `/assess` self-run on the post-WS5 tree, layer 0 verdict recorded in the PR body. Guards requirement 9. The doc-graph broken-link count in the same run covers `docs/index.md`, which no pytest test reaches.
- `git diff -M50% --name-status --diff-filter=R` on the R13 PR, output pasted into the PR body. Guards requirement 10, and is the same rename detection the R0 floor uses, so a rewrite disguised as a move fails both.
- The R10 bump-requires-changelog contract test ships with WS7 and asserts against the files requirement 14 creates. WS5 does not ship that test.

## Breadcrumbs

| Shim | Where | `remove-in` | `docs/migration-2.0.md` row |
|---|---|---|---|
| Redirect stub with a 21-row path table | `docs/superpowers/README.md` | 3.0.0 | `docs/superpowers/` to `docs/design/`, with the plans and specs subdirectory mapping |
| Three-line pointer to `REVIEW.md` | `.github/claude-review-instructions.md` | 3.0.0 | `.github/claude-review-instructions.md` to `REVIEW.md` |
| One-line pointer per moved section | `CLAUDE.md` | never | `CLAUDE.md` sections moved, one row per destination |

The three moved-section rows name `README.md` (North star), `scripts/README.md` (standalone skill pipeline), and `plugins/assess/README.md` (`/assess` architecture); the `### Release after a marathon` row names `plugins/delivery/skills/marathon/references/release-after-marathon.md`. Per the Deprecation Path in [intent.md](../intent.md), the `CLAUDE.md` pointers are permanent and the two stubs are on the 3.0 list that WS10 enumerates.

## Rollback

Every WS5 PR is revertible in isolation, and none of them touches a floor-marked file, a CI job `name:`, or branch protection.

- R7 PR: `git revert` restores the 23,581-byte `CLAUDE.md` and deletes `scripts/README.md`. The new `test_claude_md_expanded_under_8kb` reverts with it, so `main` is green at the old size. If the test lands in a separate commit, revert that commit too, or `main` fails on the restored file.
- R8 PR: `git revert` restores `.github/claude-review-instructions.md` and the workflow's line-50 read together. Because the workflow runs from the default branch, the revert takes effect on the next PR review with no re-run needed. The `INSTRUCTION_FILE_PATHS` entry and the `assess` MINOR bump revert in the same commit; the bump going backwards is safe because no tag is cut in WS5.
- R13 PR: `git revert` restores `docs/superpowers/` and the two inbound references. `test_internal_links_resolve` is the gate in both directions.
- R15 PR: `git revert` deletes the eight changelog files. No test asserts against them until WS7 lands, so a revert after WS7 has merged requires reverting WS7's contract test as well; sequence the revert that way or re-land the files.

No `v*` tag rides any WS5 commit, so no rollback is blocked by an immutable release.

## Sequencing

Four PRs, all after PR1 has merged, all parallel to each other. They touch disjoint file sets, so no merge ordering is required.

| PR | Brief | Touches | May not touch |
|---|---|---|---|
| PR-A | R7 | `CLAUDE.md`, `README.md`, `scripts/README.md`, `plugins/assess/README.md`, `plugins/delivery/skills/marathon/references/release-after-marathon.md`, `tests/test_plugin_contract.py` | `.github/`, `skills/assess/scripts/`, `docs/superpowers/`, any `CHANGELOG.md`, any file under `plugins/*/skills/*/SKILL.md` other than the new `references/` file |
| PR-B | R8 | `REVIEW.md`, `.github/claude-review-instructions.md`, `.github/workflows/claude-review.yml`, `skills/assess/scripts/assess_core.py`, `skills/assess/tests/test_assess_core.py`, `plugins/assess/.claude-plugin/plugin.json` | `CLAUDE.md`, the workflow's `ref:` line, any CI job `name:`, `docs/superpowers/` |
| PR-C | R13 | `docs/superpowers/` to `docs/design/`, `docs/design/TEMPLATE/`, `docs/index.md`, `skills/skill-forge/SKILL.md` line 159 | `CLAUDE.md`, `.github/`, `docs/design/2026-09-modernization/`, the two golden fixtures |
| PR-D | R15 | `CHANGELOG.md`, `plugins/*/CHANGELOG.md` | `plugins/*/.claude-plugin/plugin.json`, `CLAUDE.md`, `.github/`, `tests/` |

PR-B's body states that this PR is reviewed by `.github/claude-review-instructions.md`, not by the `REVIEW.md` it introduces, because `claude-review.yml` line 35 checks out the default branch; the first PR reviewed under the new policy is the next one merged after PR-B.

No WS5 PR edits `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, or `FLOOR.md`. PR-B is the only one that carries a version bump.
