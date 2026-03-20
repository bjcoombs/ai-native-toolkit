---
description: "Assess a codebase's readiness for AI agent contributors using the layered contract model"
argument-hint: "[path to repo] (defaults to current directory)"
---

# AI Readiness Assessment

Assess how ready this codebase is for sustained AI agent contribution using the layered contract model from the AI-Native Codebase Architecture guide.

**$ARGUMENTS**

## How This Works

Scan the codebase for evidence of each contract layer. For each layer, determine:
- **Present**: Evidence found and functional
- **Partial**: Some evidence but incomplete or weak
- **Missing**: No evidence found

Then score, report gaps, and recommend next steps.

## Step 1: Determine Repo Root

```bash
# If arguments provided, use that path. Otherwise use pwd.
# Find the git root from wherever we are.
git rev-parse --show-toplevel
```

Set `$REPO_ROOT` to the result. All scanning happens from here.

## Step 2: Scan Each Layer

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
- Function length limits? (funlen, max-lines-per-function)
- Complexity limits? (cyclop, complexity)
- Exhaustive matching? (exhaustive, strict unions)
- Import boundary rules? (depguard, no-restricted-imports)

**Scoring:**
- Present: Linter configured with AI failure mode rules, strict enforcement
- Partial: Linter exists but basic config, no AI-specific rules
- Missing: No linter configuration found

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

### Layer 7: Retrospective Loop (Self-Improving System)

**Scan for retro infrastructure:**
```bash
# Retro logs, feedback files
fd -t f '(retro|retrospective|feedback|learnings)' "$REPO_ROOT" 2>/dev/null
# Task Master or similar orchestration with retro capability
ls "$REPO_ROOT"/.taskmaster/ 2>/dev/null
# Check for retro-related content in instruction files
rg -i 'retrospective|retro|feedback loop|learnings' "$REPO_ROOT"/{CLAUDE.md,.cursorrules,AGENTS.md} 2>/dev/null
```

**Assess loop closure:**
- Are retros generated automatically?
- Do retros feed back into breadcrumbs/contracts?
- Is there a human approval gate?
- Evidence of breadcrumbs added from retro findings?

**Scoring:**
- Present: Structured retros generated, findings feed back into contracts
- Partial: Some retro notes exist but no systematic feedback loop
- Missing: No retrospective infrastructure

## Step 3: Score and Report

Calculate the score (0-7 based on layers present, +0.5 for partial) and generate the report.

**Output format:**

```markdown
# AI Readiness Assessment: <repo-name>

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
| 7: Retro Loop | Present/Partial/Missing | <what was found> | <what's missing> |

## Maturity Level

| Score | Level | Description |
|-------|-------|-------------|
| 0-1 | Not Ready | Agent will produce inconsistent, unvalidated code |
| 2-3 | Basic | Norms exist but aren't enforced. Agent works but drifts |
| 4-5 | Solid | Contracts catch most issues. Agent is productive |
| 6-7 | AI-Native | System self-improves. Agents work reliably at scale |

## Top 3 Actions

Prioritize by leverage: breadcrumbs and CI first, then linters and coverage,
then architecture tests and retro loops. Each action should be completable
in a single session.

| # | Action | Layer | Effort | Command / First Step |
|---|--------|-------|--------|---------------------|
| 1 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` |
| 2 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` |
| 3 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` |

### Why these three?
<2-3 sentences explaining why these are highest leverage. Connect to specific
gaps from the table above. Be concrete about what each action prevents.>

## Additional Opportunities

<If more than 3 gaps exist, list remaining as brief bullets. Keep to one line each.
These are "after you've done the top 3" items.>

## Strengths

<3-5 bullet points. What this repo already does well. Be specific — name files,
tools, and patterns. Acknowledge existing infrastructure.>
```

## Principles

- **Evidence-based**: Every assessment is backed by specific files or checks found (or not found)
- **Actionable**: Recommendations include concrete first steps, not vague advice
- **Fair**: A repo doesn't need all 7 layers to be useful. Score reflects where they are, recommendations show the path forward
- **Non-judgmental**: A score of 2 isn't "bad" — it's "here, with a clear path to 5"
- **Fast**: This should complete in under 2 minutes. Scan, don't deep-read

## No Arguments Behavior

If no path is provided, assess the current repository (from git root).
If not in a git repo, ask: "Which repository would you like me to assess?"
