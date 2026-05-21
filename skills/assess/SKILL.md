---
name: assess
description: "Assess a codebase's readiness for AI agent contributors using the layered contract model, and generate a complexity hotspot SVG treemap (size = LOC, hue = cyclomatic complexity, saturation = recent git churn). TRIGGER when the user types /assess, asks for an AI-readiness review, wants a complexity heatmap or hotspot map, asks 'how complex is this code?', wants migration risk triage, or asks for a codebase snapshot/report. Produces an MD report + SVG that can be opened as a PR in the target repo."
---

# AI Readiness Assessment + Complexity Hotspot

Two artefacts in one pass against a target repo:

1. **Layered contract assessment** — 0–7 score across breadcrumbs, types, linters, architecture tests, CI, coverage, review bots, AI project management.
2. **Complexity hotspot SVG** — Codecov-style treemap. Size = LOC. Hue = cyclomatic complexity. Saturation = recent git churn. Vivid red = complex AND active = highest risk.

Both land as files inside the target repo. The skill always writes them locally; after writing, **ask the user** whether to open a PR in the target repo with both artefacts.

**$ARGUMENTS**

## Step 1: Determine Repo Root and Output Directory

```bash
# If arguments provided, use that path. Otherwise use pwd.
# Find the git root from wherever we are.
git rev-parse --show-toplevel
```

Set `$REPO_ROOT` to the result. All scanning happens from here.

Decide the output directory (default: `$REPO_ROOT/.assess/`). Create it if needed:

```bash
mkdir -p "$REPO_ROOT/.assess"
```

Artefacts will land at:
- `$REPO_ROOT/.assess/complexity-heatmap.svg`
- `$REPO_ROOT/.assess/complexity-stats.json`
- `$REPO_ROOT/.assess/assess-report.md`

## Step 2: Generate Complexity Hotspot SVG + Stats Sidecar

Run the bundled treemap script. It produces a self-contained SVG with hover tooltips on every block **and** a JSON stats sidecar consumed by Layer 2 scoring and the Top 3 Actions table.

```bash
# Resolve the script path relative to this skill. The skill lives at
# ~/.claude/skills/assess/SKILL.md, so the script is alongside it.
SKILL_DIR="$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")"
uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" \
    -o "$REPO_ROOT/.assess/complexity-heatmap.svg" \
    --stats "$REPO_ROOT/.assess/complexity-stats.json"
```

The script prints a one-line summary (file count, lizard vs scc coverage, churn window chosen, top 5 biggest files). The stats sidecar contains percentiles (p50/p95/max for LOC, CCN, churn) and ranked lists of the top 10 files by hotspot score, raw CCN, and raw LOC. Both feed the report.

**Dependencies:** the script uses PEP 723 inline metadata (`lizard`, `squarify`, `matplotlib`, `numpy`). `uv` resolves them on first run. Optional: `brew install scc` extends coverage to 200+ languages beyond what lizard handles natively.

**If the script fails** (no `uv`, no scoreable files, etc.), record the error in the report under "Hotspot snapshot" as "could not be generated — <reason>" and continue with the layered assessment. The treemap is additive; assessment still runs without it.

## Step 3: Scan Each Layer

Run these checks in parallel where possible. For each layer, collect evidence and assess quality.

### Layer 0: Breadcrumbs (Behavioral Contracts)

**Scan for instruction files:**
```bash
# Check all known instruction file locations
ls -la "$REPO_ROOT"/{CLAUDE.md,.cursorrules,AGENTS.md,GEMINI.md,.github/copilot-instructions.md} 2>/dev/null
```

**If found, assess quality** by reading the file(s):
- Does it contain specific, actionable rules? (not just "follow best practices")
- Does it use strong language for hard rules? ("NEVER", "ALWAYS")
- Does it include technology choices and banned patterns?
- Does it reference specific files/patterns in the codebase?
- Is it current? (check git log for last modification date)

**Also scan for orientation docs** - can an agent find its way around?
```bash
# README
ls "$REPO_ROOT"/README.md 2>/dev/null
# Architecture / structure docs
fd -t f '(architecture|structure|overview|getting-started)' "$REPO_ROOT/docs" "$REPO_ROOT" --extension md 2>/dev/null | head -5
# ADRs / decision records
fd -t d '(adr|decisions|rfcs)' "$REPO_ROOT" 2>/dev/null
ls "$REPO_ROOT"/docs/adr/ "$REPO_ROOT"/docs/decisions/ 2>/dev/null | head -3
# API docs (OpenAPI, proto, AsyncAPI)
fd -t f '\.(proto|swagger|openapi)' "$REPO_ROOT" 2>/dev/null | head -5
ls "$REPO_ROOT"/{openapi,asyncapi,swagger}* 2>/dev/null
# Package/module-level docs
fd -t f 'doc\.go$' "$REPO_ROOT" 2>/dev/null | wc -l  # Go
```

**Report doc quality as a summary line:**
```
Docs: README [yes/no], architecture guide [yes/no], ADRs [N found], API specs [N found], package docs [N found]
```

Missing docs don't reduce the layer score (breadcrumbs are the primary signal), but flag gaps in the report. An agent with good breadcrumbs but no README or architecture docs can follow rules but can't orient itself in unfamiliar parts of the codebase.

**Scoring:**
- Present: Instruction file exists with specific, actionable breadcrumbs
- Partial: File exists but is generic, stale, or mostly boilerplate
- Missing: No instruction files found

### Layer 1: Code Design (Compile-Time Correctness)

**Scan for type safety indicators** (check whichever languages are present):
```bash
# TypeScript: strict mode
rg '"strict"\s*:\s*true' "$REPO_ROOT" --glob 'tsconfig*.json' --type json
# Go: generics, custom types
rg 'type\s+\w+\[' "$REPO_ROOT" --type go --head-limit 5
# Python: mypy, pyright, type hints
ls "$REPO_ROOT"/{mypy.ini,pyrightconfig.json,.mypy.ini} 2>/dev/null
rg 'tool.mypy|tool.pyright' "$REPO_ROOT/pyproject.toml" 2>/dev/null
# Java/Kotlin: records, sealed, final classes
rg '^\s*(public\s+)?record\s+' "$REPO_ROOT" --type java --head-limit 5
rg 'sealed\s+(class|interface)' "$REPO_ROOT" --type java --type kotlin --head-limit 5
# C#: nullable enable, records
rg '<Nullable>enable</Nullable>' "$REPO_ROOT" --glob '*.csproj' --head-limit 3
# Rust: inherently type-safe, check for unsafe blocks
rg 'unsafe\s*\{' "$REPO_ROOT" --type rust | wc -l
# Ruby: Sorbet type checking
ls "$REPO_ROOT"/sorbet/ 2>/dev/null
rg 'typed:\s*(true|strict|strong)' "$REPO_ROOT" --type ruby --head-limit 3
# Dart: null safety
rg 'sdk:\s*.>=\s*[23]\.' "$REPO_ROOT/pubspec.yaml" 2>/dev/null
```

**Sample code for patterns** (read 2-3 files from core modules):
- Immutability patterns (const, readonly, final, value types)
- Pure functions vs side-effect-heavy code
- Custom types vs primitive obsession

**Scoring:**
- Present: Strict type checking enabled, custom domain types, immutability patterns visible
- Partial: Type checking exists but not strict, or inconsistent patterns
- Missing: No type checking, primitive types everywhere, mutable state default

### Layer 2: Linters (Style and Correctness Enforcement)

**Scan for linter configuration** (check whichever languages are present):
```bash
# Go
ls "$REPO_ROOT"/{.golangci.yml,.golangci.yaml} 2>/dev/null
# JS/TS
ls "$REPO_ROOT"/{.eslintrc*,eslint.config.*,biome.json,.prettierrc*} 2>/dev/null
rg '"lint"' "$REPO_ROOT/package.json" 2>/dev/null
# Python
ls "$REPO_ROOT"/{ruff.toml,.ruff.toml,.flake8,.pylintrc} 2>/dev/null
rg 'tool.ruff|tool.pylint|tool.flake8' "$REPO_ROOT/pyproject.toml" 2>/dev/null
# Java/Kotlin
rg 'checkstyle|spotbugs|pmd|spotless|error-prone|detekt' "$REPO_ROOT"/{pom.xml,build.gradle*} 2>/dev/null
# C#
ls "$REPO_ROOT"/{.editorconfig,*.ruleset,Directory.Build.props} 2>/dev/null
rg 'EnableNETAnalyzers|AnalysisLevel' "$REPO_ROOT" --glob '*.csproj' --head-limit 3
# Ruby
ls "$REPO_ROOT"/.rubocop.yml 2>/dev/null
# Rust
ls "$REPO_ROOT"/clippy.toml "$REPO_ROOT"/.clippy.toml 2>/dev/null
# Dart
ls "$REPO_ROOT"/analysis_options.yaml 2>/dev/null
# Swift
ls "$REPO_ROOT"/.swiftlint.yml 2>/dev/null
```

**If found, assess AI-relevant rules** by reading the config:
- Unexplained lint suppression rules? (nolintlint, no-restricted-syntax)
- TODO/FIXME detection? (godox, no-warning-comments)
- **Function length limits?** (`funlen`, `max-lines-per-function`, `MethodLength`, `function-max-lines`)
- **Cyclomatic complexity limits?** (`cyclop`, `gocognit`, `complexity`, `CyclomaticComplexity`, `too-many-statements`, `cognitive-complexity`, `cognitive_complexity`)
- **File size limits?** (`max-lines`, `FileLength`, `file-max-lines`, `lines-per-file`)
- Exhaustive matching? (exhaustive, strict unions)
- Import boundary rules? (depguard, no-restricted-imports)

**Cross-reference treemap evidence.** Read the stats sidecar to see what the linter actually catches in the wild — but only if Step 2 produced it. The sidecar is missing whenever the treemap script failed (no `uv`, non-git path, no scoreable files, etc.).

```bash
STATS="$REPO_ROOT/.assess/complexity-stats.json"
if [ -f "$STATS" ]; then
  jq '{
    loc_p95: .loc.p95, loc_max: .loc.max,
    ccn_p95: .ccn.p95, ccn_max: .ccn.max,
    worst_complex: .top_complex[:3] | map(.path),
    worst_large: .top_large[:3] | map(.path)
  }' "$STATS"
else
  echo "complexity-stats.json not present; scoring Layer 2 on linter config alone."
fi
```

If the sidecar is missing, skip the combined-scoring matrix below and fall back to the original Layer 2 rule: Present if linter config includes AI-relevant rules (including complexity/length), Partial if linter exists without them, Missing if no linter at all. Record "treemap unavailable" in the Evidence column so the gap is auditable.

Thresholds for "high" (based on industry conventions — adjust for context):

| Signal | Watch | High |
|---|---|---|
| p95 cyclomatic complexity | ≥ 10 | ≥ 15 |
| max cyclomatic complexity | ≥ 30 | ≥ 50 |
| p95 file size (LOC) | ≥ 500 | ≥ 800 |
| max file size (LOC) | ≥ 1500 | ≥ 2000 |

**Combined scoring** (linter config ∩ treemap evidence):

| Linter has complexity/length rules? | Treemap p95 / max in "High" range? | Score |
|---|---|---|
| Yes, enforced (CI blocks) | Either way — rules ratchet the legacy | **Present** |
| Yes but lenient / excludes legacy | High | **Partial** — rules exist, legacy unfenced |
| Linter exists, no complexity/length rules | Not high | **Partial** — gap but no evidence yet |
| Linter exists, no complexity/length rules | High | **Missing** — concrete evidence of the gap |
| No linter at all | Either | **Missing** |

When scoring Partial or Missing on this combined check, name the top 3 worst offenders from `top_complex` / `top_large` in the report's Evidence/Gap columns. Those are the files the missing rule would have flagged.

### Layer 3: Architecture Tests (Conventions as Contracts)

**Scan for architecture test files:**
```bash
# Common architecture test locations
fd -t f '(architect|convention|structure|boundary)' "$REPO_ROOT" --extension go --extension ts --extension js --extension py --extension java 2>/dev/null
fd -t d 'architecture' "$REPO_ROOT/tests" "$REPO_ROOT/test" 2>/dev/null
# ArchUnit (Java/Kotlin)
rg 'import.*archunit|ArchTest|@ArchTest' "$REPO_ROOT" --type java --type kotlin 2>/dev/null | head -5
# Convention check scripts
ls "$REPO_ROOT"/scripts/*convention* "$REPO_ROOT"/scripts/*verify* 2>/dev/null
```

**If found, assess coverage:**
- Do they enforce file/function size limits?
- Do they enforce import boundaries?
- Do they enforce structural consistency across services/modules?
- Is there a ratchet pattern for existing violations?

**Scoring:**
- Present: Architecture tests enforce structure, boundaries, and naming in CI
- Partial: Some convention scripts exist but incomplete coverage
- Missing: No architecture tests or convention enforcement

### Pre-check: Test Inventory

Before assessing CI and coverage, count what's actually there to run. A repo with zero tests makes every downstream layer meaningless.

```bash
# Count test files by language (detect what's in use, don't assume)
fd -t f '_test\.go$' "$REPO_ROOT" 2>/dev/null | wc -l                          # Go
fd -t f '\.(test|spec)\.(ts|tsx|js|jsx|mjs)$' "$REPO_ROOT" 2>/dev/null | wc -l # JS/TS (jest, vitest, mocha)
fd -t f '(Test|IT|Tests)\.java$' "$REPO_ROOT" 2>/dev/null | wc -l              # Java (JUnit, TestNG)
fd -t f '(Test|IT|Tests)\.kt$' "$REPO_ROOT" 2>/dev/null | wc -l                # Kotlin
fd -t f '(test_.*|.*_test)\.py$' "$REPO_ROOT" 2>/dev/null | wc -l              # Python (pytest, unittest)
fd -t f '_test\.rs$' "$REPO_ROOT" 2>/dev/null | wc -l                          # Rust
fd -t f '(Test|Spec)\.cs$' "$REPO_ROOT" 2>/dev/null | wc -l                    # C# (xUnit, NUnit)
fd -t f '_test\.rb$' "$REPO_ROOT" 2>/dev/null | wc -l                          # Ruby (RSpec, minitest)
fd -t f '_test\.dart$' "$REPO_ROOT" 2>/dev/null | wc -l                        # Dart/Flutter
fd -t f '\.test\.swift$' "$REPO_ROOT" 2>/dev/null | wc -l                      # Swift (XCTest)

# Only report languages with >0 test files. Skip the rest.

# Categorize: unit vs integration vs e2e (look at directory names and file names)
fd -t f -p '(integration|e2e|acceptance|functional|contract)' "$REPO_ROOT" 2>/dev/null | wc -l
```

**Report as a summary line in the output:**
```
Tests: <N> test files (<M> unit, <K> integration/e2e) across <languages>
```

**If zero tests found**, flag prominently:
> **No tests detected.** CI pipeline, coverage gates, and review bots have nothing to validate against. Writing tests is the prerequisite for every other layer.

This should bump the test-less repo's score down and make "add tests" the #1 action.

### Layer 4: CI Pipeline (Automated Safety Net)

**Scan for CI configuration:**
```bash
# Common CI configs
ls -la "$REPO_ROOT"/.github/workflows/*.yml "$REPO_ROOT"/.github/workflows/*.yaml 2>/dev/null
ls -la "$REPO_ROOT"/{.gitlab-ci.yml,.circleci/config.yml,Jenkinsfile,.travis.yml,bitbucket-pipelines.yml} 2>/dev/null
```

**If found, assess pipeline completeness** by reading CI configs:
- Build/compile step?
- Lint step?
- Unit test step?
- Integration test step?
- Architecture test step?
- Coverage reporting?
- Security scanning?
- Generated file freshness checks?

**Check if failures are blocking** (not just advisory):
```bash
# GitHub: check branch protection
gh api repos/{owner}/{repo}/branches/main/protection 2>/dev/null || \
gh api repos/{owner}/{repo}/branches/develop/protection 2>/dev/null
```

**Scoring:**
- Present: CI runs on every PR with build+lint+test+coverage, failures block merge
- Partial: CI exists but missing key steps, or failures are advisory
- Missing: No CI configuration found

### Layer 5: Coverage Gates (Test Completeness Enforcement)

**Scan for coverage configuration:**
```bash
ls -la "$REPO_ROOT"/{codecov.yml,.codecov.yml,codecov.yaml,coveralls.yml,.coveragerc,jest.config.*,vitest.config.*} 2>/dev/null
# Check for coverage thresholds in config
rg 'threshold|min_coverage|coverageThreshold|branches.*[0-9]' "$REPO_ROOT"/{codecov.yml,.codecov.yml,jest.config.*,vitest.config.*} 2>/dev/null
# Check CI for coverage gates
rg 'coverage|codecov|coveralls' "$REPO_ROOT"/.github/workflows/*.yml 2>/dev/null | head -5
```

**Assess gate strictness:**
- Project-wide minimum threshold?
- Per-PR patch coverage requirement?
- Per-component thresholds?
- Does CI fail on coverage regression?

**Scoring:**
- Present: Coverage gates block PRs below threshold, patch coverage enforced
- Partial: Coverage reported but not enforced, or thresholds too low
- Missing: No coverage configuration or gates

### Layer 6: Automated Code Review (Design-Level Feedback)

**Scan for review bot configuration:**
```bash
ls -la "$REPO_ROOT"/{.coderabbit.yaml,.coderabbit.yml,.github/copilot-review.yml} 2>/dev/null
# Check for review bot in CI
rg 'coderabbit|copilot|codeclimate|sonarqube|sonarcloud' "$REPO_ROOT"/.github/workflows/*.yml 2>/dev/null | head -5
# Check if bots are active on recent PRs
gh pr list --limit 5 --json number --jq '.[].number' 2>/dev/null | head -3 | while read PR; do
  gh api repos/{owner}/{repo}/pulls/$PR/comments --jq '.[].user.login' 2>/dev/null | sort -u | head -5
done
```

**Scoring:**
- Present: Automated review bot active on PRs, providing design-level feedback
- Partial: Bot configured but not active, or only running basic checks
- Missing: No automated code review

### Layer 7: AI Project Management (Orchestration and Feedback)

Does the project treat AI agents as contributors to plan around - with structured task management, workflow orchestration, and a feedback loop that improves the system over time?

**Scan for AI orchestration tooling:**
```bash
# Task/workflow orchestration
ls "$REPO_ROOT"/.taskmaster/ 2>/dev/null          # Task Master
ls "$REPO_ROOT"/.speckit/ 2>/dev/null              # SpecKit
ls "$REPO_ROOT"/.gsd/ 2>/dev/null                  # GSD
ls "$REPO_ROOT"/.sweep/ 2>/dev/null                # Sweep
ls "$REPO_ROOT"/.devin/ 2>/dev/null                # Devin
ls "$REPO_ROOT"/.aider* 2>/dev/null                # Aider
ls "$REPO_ROOT"/.continue/ 2>/dev/null             # Continue
fd -t f '(kanban|backlog|sprint|iteration)' "$REPO_ROOT" --extension md --extension json --extension yaml 2>/dev/null | head -3
```

**Scan for feedback loop infrastructure:**
```bash
# Retro logs, learnings, postmortems
fd -t f '(retro|retrospective|feedback|learnings|postmortem|post-mortem)' "$REPO_ROOT" 2>/dev/null
# Feedback references in instruction files
rg -i 'retrospective|retro|feedback loop|learnings|post.?mortem' "$REPO_ROOT"/{CLAUDE.md,.cursorrules,AGENTS.md} 2>/dev/null
```

**Assess across three dimensions:**

1. **Task orchestration** - Are AI tasks structured and tracked? (Task Master tags, SpecKit specs, GSD tasks, GitHub Projects with AI labels, etc.)
2. **Feedback loop** - Do learnings feed back into contracts? (retro logs, breadcrumb updates traced to incidents, iterative CLAUDE.md refinement)
3. **Workflow maturity** - Is there evidence of repeated AI work cycles? (multiple completed tags/sprints, merged PR history from AI branches, wave-based orchestration)

**Scoring:**
- Present: Structured AI task management with feedback loop that updates contracts
- Partial: Some orchestration tooling exists but no systematic feedback loop, or ad-hoc retro notes without structured process
- Missing: No AI-aware project management

## Step 4: Score and Write the Report

Calculate the score (0-7 based on layers present, +0.5 for partial) and write the report to `$REPO_ROOT/.assess/assess-report.md`.

**Report format** (write this to disk verbatim, filling in the placeholders):

```markdown
# Codebase Assessment: <repo-name>

_Generated <YYYY-MM-DD>._

## How to read this report

This is an improvement roadmap, not a verdict. It pairs two views:

- **Where the codebase is today** — the hotspot SVG shows current complexity and churn at a glance. Vivid red = complex AND actively changing = the files most likely to bite an agent (or a human) next week.
- **What scaffolding is in place to keep it from getting worse** — the 7-layer AI Readiness score measures whether the system enforces contracts that catch the issues the hotspots reveal.

A codebase can be 7/7 and still on fire (great scaffolding, legacy debt) — or 2/7 with a calm treemap (small codebase, no enforcement needed yet). The pair matters.

The "Top 3 Actions" table at the bottom names specific files. Start there.

## Hotspot snapshot

![Complexity hotspot](./complexity-heatmap.svg)

- **Files scored:** <N>
- **Churn window chosen:** <last 12mo | last 24mo | last 5y | all-time>
- **Complexity profile:** p95 ccn <N> (max <M>); p95 LOC <N> (max <M>)
- **Top hotspots** (composite: complexity × recent churn):
  1. `<path>` — <loc> LOC, ccn <N>, <M> commits in window
  2. ...
  3. ...

Size encodes lines of code, hue encodes cyclomatic complexity (red = high), saturation encodes recent git churn (vivid = active). Vivid red blocks are the migration risk.

## AI Readiness

**Score: X / 7** — <maturity-label>

| Layer | Status | Evidence | Gap |
|-------|--------|----------|-----|
| 0: Breadcrumbs | Present/Partial/Missing | <what was found> | <what's missing> |
| 1: Code Design | Present/Partial/Missing | <what was found> | <what's missing> |
| 2: Linters | Present/Partial/Missing | <what was found> | <what's missing> |
| 3: Architecture Tests | Present/Partial/Missing | <what was found> | <what's missing> |
| 4: CI Pipeline | Present/Partial/Missing | <what was found> | <what's missing> |
| 5: Coverage Gates | Present/Partial/Missing | <what was found> | <what's missing> |
| 6: Code Review Bots | Present/Partial/Missing | <what was found> | <what's missing> |
| 7: AI Project Mgmt | Present/Partial/Missing | <what was found> | <what's missing> |

### Maturity Level

| Score | Level | Description |
|-------|-------|-------------|
| 0-1 | Not Ready | Agent will produce inconsistent, unvalidated code |
| 2-3 | Basic | Norms exist but aren't enforced. Agent works but drifts |
| 4-5 | Solid | Contracts catch most issues. Agent is productive |
| 6-7 | AI-Native | System self-improves. Agents work reliably at scale |

## Top 3 Actions

Prioritize by leverage: breadcrumbs and CI first, then linters and coverage, then architecture tests and retro loops. Each action should be completable in a single session and reference **specific files** from the hotspot snapshot wherever possible — generic advice is the failure mode this report exists to prevent.

| # | Action | Layer | Effort | Command / First Step | Hotspot files this addresses |
|---|--------|-------|--------|---------------------|------------------------------|
| 1 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths from top_hotspots / top_complex / top_large, or "—" if not file-specific> |
| 2 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths or —> |
| 3 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths or —> |

Good actions look like:

> _"Add `cyclop` rule (threshold 15) to `.golangci.yml`. Current p95 ccn is 23; immediate offenders: `internal/import/parser.go` (ccn 67), `internal/sync/reconciler.go` (ccn 54)."_

Generic actions to avoid:

> ~~_"Improve code quality"_~~ — name the files and the threshold.
> ~~_"Add a linter"_~~ — name the linter, the rule, and the first three files it will flag.

### Why these three?
<2-3 sentences explaining why these are highest leverage. Connect to specific gaps from the table above and to hotspot files where relevant. Be concrete about what each action prevents.>

## Additional Opportunities

<If more than 3 gaps exist, list remaining as brief bullets. Keep to one line each. These are "after you've done the top 3" items.>

## Strengths

<3-5 bullet points. What this repo already does well. Be specific — name files, tools, and patterns. Acknowledge existing infrastructure.>

---

_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit). Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
```

Complete the full assessment in under 2 minutes. Scan, don't deep-read.

The SVG is referenced by relative path (`./complexity-heatmap.svg`) so GitHub renders it inline when the MD is viewed in a PR or on the file page.

The plugin footer is important — it's how other engineers viewing the report in a PR discover the tool that produced it. Do not omit it.

## Step 5: Ask Whether to Open a PR

After writing both files, surface them and ask the user — verbatim — something like:

> Wrote `.assess/assess-report.md` and `.assess/complexity-heatmap.svg` in `<repo-name>`. Want me to open a PR in this repo with both files, or leave them local for you to review first?

If the user says **yes / PR**:
1. Create a branch in the target repo: `assess/snapshot-<YYYY-MM-DD>` (use the existing worktree workflow if `<repo>-main` + `worktree/` layout is present; otherwise branch in place).
2. Stage and commit both files. Commit message: `docs: Add AI-readiness assessment + complexity hotspot snapshot`.
3. Push the branch and open a PR. Title: `docs: Codebase assessment — <YYYY-MM-DD>`.
4. **PR body must include the plugin reference at the bottom** so reviewers can install the tool that generated the report. Use this body template:

   ```markdown
   ## Summary

   Snapshot of this codebase's AI-agent readiness and complexity hotspots as of <YYYY-MM-DD>.

   - **AI Readiness:** <X / 7> — <maturity-label>
   - **Hotspot leader:** `<top hotspot path>` (<loc> LOC, ccn <N>, <M> commits in window)

   ## Top 3 Actions

   <paste the Top 3 Actions table from .assess/assess-report.md verbatim>

   Full report: [`.assess/assess-report.md`](./.assess/assess-report.md) (the complexity heatmap renders inline).

   ---

   _Generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit) — a Claude Code plugin for codebase readiness assessment with complexity hotspot heatmaps. Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
   ```

If the user says **no / leave it**: stop. Files stay in `.assess/` for them to review — the plugin footer in the MD already advertises the tool when anyone opens the file.
