---
name: assess
description: "Assess a codebase's readiness for AI agent contributors using the layered contract model, and generate a complexity hotspot SVG treemap (size = LOC, hue = cyclomatic complexity, saturation = recent git churn). TRIGGER when the user types /assess, asks for an AI-readiness review, wants a complexity heatmap or hotspot map, asks 'how complex is this code?', wants migration risk triage, or asks for a codebase snapshot/report. Produces an MD report + SVG that can be opened as a PR in the target repo."
---

# AI Readiness Assessment + Complexity Hotspot

Two artefacts in one pass against a target repo:

1. **Layered contract assessment** — 0–7 score across agent instructions, types, linters, architecture tests, CI, coverage, review bots, AI project management.
2. **Complexity hotspot SVG** — Codecov-style treemap. Size = LOC. Hue = cyclomatic complexity. Saturation = recent git churn. Vivid red = complex AND active = highest risk.

Both land as files inside the target repo. The skill always writes them locally; after writing, **ask the user** whether to open a PR in the target repo with both artefacts.

<!-- chat-skip:start -->
**$ARGUMENTS**
<!-- chat-skip:end -->

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

### 2a: Offer to install `scc` (one-time per repo)

The bundled treemap uses [`lizard`](https://github.com/terryyin/lizard) (Python, Go, JS, Java, C/C++, etc.) by default. Optional `scc` extends coverage to 200+ languages including markdown, JSON, YAML, SQL, and shell — useful when the repo's surface is more than just traditional source code.

Before scanning, check three signals:

```bash
# 1. Is scc already on PATH?
command -v scc >/dev/null 2>&1 && SCC_PRESENT=1 || SCC_PRESENT=0

# 2. Has the user previously declined for this repo?
[ -f "$REPO_ROOT/.assess/.no-scc" ] && SCC_DECLINED=1 || SCC_DECLINED=0

# 3. Is the repo mostly markdown/data/config (where lizard alone will be sparse)?
#    Cheap heuristic: count non-code files vs code files.
CODE_FILES=$(fd -t f -e py -e js -e ts -e tsx -e jsx -e go -e java -e kt -e rs -e rb -e cs -e swift -e dart -e cpp -e c -e h -e php "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
NONCODE_FILES=$(fd -t f -e md -e json -e yaml -e yml -e toml -e sh -e sql "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
```

**Offer the install only if all three are true:** `SCC_PRESENT=0`, `SCC_DECLINED=0`, and the repo looks lizard-sparse (`CODE_FILES < NONCODE_FILES` or `CODE_FILES < 10`). Otherwise skip straight to 2b.

When offering, use **AskUserQuestion** with three options (do **not** auto-install — `brew install` is a system mutation):

- **Install scc** — run the appropriate installer for the platform and continue.
- **Skip for now** — proceed with lizard only. Don't write the marker; ask again next run.
- **Skip permanently for this repo** — write `$REPO_ROOT/.assess/.no-scc` so future runs don't ask. Recommended for prompt repos or pure-docs repos where lizard-only is genuinely fine.

Phrase the question so the user understands the trade-off, e.g.:

> "This repo has <N> code files and <M> non-code files (markdown/JSON/YAML). `scc` would include the non-code files in the treemap; without it the treemap may be sparse. Install `scc`?"

If the user picks **Install scc**, run the platform-appropriate command:

```bash
# macOS (Homebrew)
[ "$(uname)" = "Darwin" ] && command -v brew >/dev/null && brew install scc

# Linux (try common package managers, fall back to go install or manual)
[ "$(uname)" = "Linux" ] && {
  command -v apt >/dev/null && sudo apt install -y scc \
    || command -v dnf >/dev/null && sudo dnf install -y scc \
    || command -v go >/dev/null && go install github.com/boyter/scc/v3@latest \
    || echo "Install scc manually: https://github.com/boyter/scc#installation"
}
```

If the install fails or the platform isn't covered, fall back to lizard-only and continue — don't block the assessment.

### 2b: Run the treemap

Run the bundled treemap script alongside the deterministic core - see the chained block below.

The script prints a one-line summary (file count, lizard vs scc coverage, churn window chosen, top 5 biggest files). The stats sidecar contains percentiles (p50/p95/max for LOC, CCN, churn) and ranked lists of the top 10 files by hotspot score, raw CCN, and raw LOC. Both feed the report.

**Dependencies:** the script uses PEP 723 inline metadata (`lizard`, `squarify`, `matplotlib`, `numpy`). `uv` resolves them on first run.

**Build artifacts and generated code are filtered by default.** The script excludes two classes of files:

- **Build artifacts**: `main.dart.js`, `*.min.js`, `*.bundle.js`, `*.chunk.js`, `*.map`, sourcemaps, service workers, and files under `node_modules/`, `dist/`, `build/`, `.next/`, `.nuxt/`, `.output/`, `coverage/`, etc.
- **Generated code**: protobuf bindings (`*.pb.go`, `*_grpc.pb.go`, `*.pb.gw.go`, `*.connect.go`, `*_pb.ts`, `*_pb.d.ts`, `*_pb2.py`, `*.pb.cc`, `*.pb.h`), Go generators (`*.gen.go`, `wire_gen.go`, `zz_generated_*.go`, `bindata.go`), .NET source generators (`*.designer.cs`, `*.g.cs`), Dart/Flutter codegen (`*.freezed.dart`, `*.g.dart`, `*.gr.dart`).

Full list in `complexity-treemap.py`'s `EXCLUDE_DIRS` and `EXCLUDE_FILE_PATTERNS`. If you specifically want to score these (e.g., to visualise how much of the repo is generated), pass `--include-artifacts`.

**Dominance warning.** If a single file still holds >30% of total scoreable LOC after filtering (the threshold compiled bundles typically cross), the script prints a warning to stderr identifying the file. When you see this:

- Surface it in the report's "Hotspot snapshot" section as a finding: "`<file>` holds X% of LOC and is likely a build artifact - recommend adding to `.gitignore` and re-running."
- Add a Top 3 Action: "Add `<file>` (and similar compiled outputs) to `.gitignore` and remove from tracking. Re-run `/assess` to get a meaningful treemap."
- Do NOT skip the rest of the assessment - the layered scan still produces useful signal.

**If the script fails** (no `uv`, no scoreable files, etc.), record the error in the report under "Hotspot snapshot" as "could not be generated — <reason>" and continue with the layered assessment. The treemap is additive; assessment still runs without it.

Run the full sequence - rotate the prior sidecar first, then the treemap, then the deterministic core:

```bash
# Rotate the prior stats sidecar so the diff has something to compare against next run
if [ -f "$REPO_ROOT/.assess/complexity-stats.json" ]; then
  cp "$REPO_ROOT/.assess/complexity-stats.json" "$REPO_ROOT/.assess/complexity-stats.prior.json" 2>/dev/null || true
fi

# Resolve the script path relative to this skill. The skill lives at
# ~/.claude/skills/assess/SKILL.md, so the script is alongside it.
<!-- chat-skip:start -->
SKILL_DIR="$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")"
<!-- chat-skip:end -->

# Run the treemap (produces fresh complexity-stats.json)
<!-- chat-replace:uv-treemap -->
uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" \
    -o "$REPO_ROOT/.assess/complexity-heatmap.svg" \
    --stats "$REPO_ROOT/.assess/complexity-stats.json"

# Run the deterministic core (instruction-file grading, stats diff, wiki files, run-context.json)
<!-- chat-replace:uv-core -->
uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT"
```

Now `$REPO_ROOT/.assess/run-context.json` contains the structured data you need for the prose sections. Read it before writing the report.

The `plugin_version` field in `run-context.json` tells you which plugin version produced this run. Surface it at the top of the report (e.g., "Generated by `/assess` v1.5.0") so readers can spot it if a stale cached version of the plugin produced unexpected output.

## Step 3: Scan Each Layer

Run these checks in parallel where possible. For each layer, collect evidence and assess quality.

### Layer 0: Agent Instructions (Behavioral Contracts)

Read the agent instruction file grades from `run-context.json`:

```bash
jq '.instruction_files, .instructions_grade' "$REPO_ROOT/.assess/run-context.json"
```

`.instruction_files` is a dict keyed by filename (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md`). The same heuristic grader scores each present file. For each:
- `grade: "A" | "B+" | ...` - report verbatim per file
- `subscores.positive_directives` - count of positive directives found
- `subscores.tradeoff_phrases` - count of reasoning phrases
- `subscores.path_references` - count of file path references
- `freshness_days` - days since last edit

`.instructions_grade` is the best grade across all present files - use this for the layer scoring.

**Scoring rule:** trust the deterministic grade.
- `instructions_grade` is `null` → **Missing** (no instruction file found at any known location)
- A/A-/B+ → **Present**
- B/C → **Partial**
- D/F → **Partial** if at least one file exists but scores low - note the grade and recommend rewriting

Important: a null grade and an F grade map to different remediation. Null means "create a CLAUDE.md / AGENTS.md (whichever the team uses)." F means "the file is there but needs rewriting." Don't conflate them in the report.

When multiple instruction files are present (e.g. CLAUDE.md and AGENTS.md as symlinks of the same content), list each in the report.

This replaces the prior subjective "is it generic?" check. The grader rewards positive directives and tradeoff reasoning; it penalizes pure-negative framing and staleness.

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

Missing docs don't reduce the layer score (agent instructions are the primary signal), but flag gaps in the report. An agent with good agent instructions but no README or architecture docs can follow rules but can't orient itself in unfamiliar parts of the codebase.

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
2. **Feedback loop** - Do learnings feed back into contracts? (retro logs, agent instruction updates traced to incidents, iterative CLAUDE.md refinement)
3. **Workflow maturity** - Is there evidence of repeated AI work cycles? (multiple completed tags/sprints, merged PR history from AI branches, wave-based orchestration)

**Scoring:**
- Present: Structured AI task management with feedback loop that updates contracts
- Partial: Some orchestration tooling exists but no systematic feedback loop, or ad-hoc retro notes without structured process
- Missing: No AI-aware project management

## Step 3.5: Read Cross-Run Context

Before scoring, check what changed since the last run:

```bash
jq '.diff, .diff_detail' "$REPO_ROOT/.assess/run-context.json"
```

If `prior` was None (first run), skip this section in the report. Otherwise, populate a "What Changed Since Last Run" section in the report:

- **Graduated** (good): list paths from `diff_detail.graduated` - hotspots that left the top list
- **Regressed** (bad): list paths from `diff_detail.regressed` with their `ccn_delta` / `commits_delta`
- **New** (watch): list paths from `diff_detail.new`
- **Persistent** (structural debt if N runs in a row): list paths from `diff_detail.persistent`

The wiki files at `.assess/index.md` and `.assess/hotspots/*.md` are already updated by `assess_core.py` - you don't need to write them. You only write the prose summary in `assess-report.md`.

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
| 0: Agent Instructions | Present/Partial/Missing | <what was found> | <what's missing> |
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

Prioritize by leverage: agent instructions and CI first, then linters and coverage, then architecture tests and retro loops. Each action should be completable in a single session and reference **specific files** from the hotspot snapshot wherever possible — generic advice is the failure mode this report exists to prevent.

| # | Action | Layer | Effort | Command / First Step | Hotspot files this addresses | Issue |
|---|--------|-------|--------|---------------------|------------------------------|-------|
| 1 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths from top_hotspots / top_complex / top_large, or "—" if not file-specific> | — |
| 2 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths or —> | — |
| 3 | <one-line action> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths or —> | — |

The `Issue` column is filled in by Step 6 if the user opts to create tracking issues. Leave as `—` initially.

**Frame actions positively.** "Add `cyclop` rule (threshold 15) to `.golangci.yml`" beats "Stop letting complex code through CI." Positive directives are easier for the next contributor (human or LLM) to act on - they say what to do, not what to avoid. If you find yourself writing "Don't X" or "Never Y", convert to "Use X (because Z)" instead.

**Use repo-relative paths only.** Never write absolute paths from your environment (e.g. `/Users/.../repo/src/foo.go`) into the report. They leak the author's directory layout, break shell commands for other contributors, and look unprofessional in committed artifacts. Repo-relative paths (`src/foo.go`, `.golangci.yml`) work everywhere.

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

**Wiki:** see `.assess/index.md` for the full hotspot catalog across all runs, `.assess/log.md` for run history, and `.assess/hotspots/<file>.md` for per-file briefings.

---

<!-- chat-replace:report-footer -->
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

   <!-- chat-replace:pr-footer -->
   _Generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit) — a Claude Code plugin for codebase readiness assessment with complexity hotspot heatmaps. Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
   ```

If the user says **no / leave it**: stop. Files stay in `.assess/` for them to review — the plugin footer in the MD already advertises the tool when anyone opens the file.

**Gitignore hint:** suggest the user add `.assess/complexity-stats.prior.json` to their `.gitignore`. It's a transient rotation file that the next run overwrites; keeping it tracked creates noisy diffs. The current stats (`complexity-stats.json`) should still be committed - it's the baseline for the next run's diff.

## Step 6: Offer to Track the Top 3 Actions in the User's Issue Tracker

After Step 5 (whether a PR was opened or not), surface a separate question:

> Want me to create tracking items for the Top 3 Actions in your issue tracker? Each becomes a closeable, assignable work item rather than a bullet buried in a PR description.

If the user says **no**: stop. The Top 3 Actions table in the PR/report still lists everything inline.

If the user says **yes**, proceed agnostically - **don't assume GitHub** (or any specific tracker). Use your judgment based on what's actually in front of you.

### 6a: Identify the user's issue tracker

Look at every signal available and pick the one the user actually uses. Examples of signals (not an exhaustive list):

- **The user's global instructions** (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`) often state the tracker explicitly: "issues live in Linear project FOO", "we use Task Master", "Jira project ABC", etc.
- **Project-level instructions** in the target repo's `CLAUDE.md` / `AGENTS.md` / contribution docs.
- **Project files**: `.taskmaster/` directory, `.acli/` config, `.linear/` dotfiles, GitHub/GitLab remote, a Notion link in the README, etc.
- **Authenticated CLIs**: `gh auth status`, `glab auth status`, `acli` configuration, `linear` CLI tokens, anything similar.
- **Conversation history**: if the user just used a tracker, prefer that one.

This is not a fixed list. The user might track work in Omnifocus, Apple Notes, a Google Doc, a Notion database, a Slack channel, or anything else. **Use judgment**, don't enumerate.

**Decision rules:**

1. **One clear signal** (e.g. only `.taskmaster/` present, or global CLAUDE.md says "use Linear") - use that tracker without asking.
2. **Multiple plausible signals** (e.g. GitHub remote + `.taskmaster/` + Jira project mentioned) - **ask the user**. List what you saw and let them pick:
   > I see signals for both Task Master (`.taskmaster/` directory) and GitHub Issues (GitHub remote, `gh` authenticated). Which should I create the tracking items in?
3. **No clear signal** - ask:
   > I couldn't tell which tracker you use. Where should I create the tracking items? (e.g. GitHub Issues, Task Master, Jira, Linear, somewhere else - or skip)

When asking, use **AskUserQuestion** with the detected options plus a "something else" / "skip" escape hatch.

### 6b: Create items in the chosen tracker

Once the tracker is known, use its native tooling. The skill doesn't enumerate every CLI - rely on your general knowledge of the tool. A few examples:

| Tracker | Typical command |
|---|---|
| GitHub Issues | `gh issue create --label assess-finding --title "..." --body "..."` |
| GitLab Issues | `glab issue create --label assess-finding ...` |
| Jira (via acli) | `acli workitem create ...` (see project's Atlassian instructions) |
| Task Master | `task-master add-task --prompt "..."` (under the current tag, or ask) |
| Linear (via CLI) | `linear issue create ...` |
| Anything else | follow the user's documented convention |

For each Top 3 Action, the item should contain:

- **Title**: the action title verbatim from the Top 3 table
- **Body**: the action detail (Command / First Step, Hotspot files, the "Why these three" reasoning), a small metadata block (Layer, Effort, link to the assessment PR if one was opened), and a one-line footer linking back to the plugin
- **Tag / Label / Category** that supports idempotency (see 6c)

Example minimal body shape (adapt to the tracker's conventions):

```markdown
<action one-line>

<Command / First Step>

<Hotspot files this addresses>

<Why-this-action reasoning>

### From /assess
- Layer: <N>: <name>
- Effort: <small | medium | large>
- Assessment PR: <PR link or omit if no PR>

---
Generated by /assess - https://github.com/bjcoombs/ai-native-toolkit
```

### 6c: Idempotency

Re-running `/assess` on the same repo must not create duplicate tracking items. The dedup mechanism depends on the tracker:

- **Tag/label-based** (GitHub, GitLab, Linear, Jira labels): apply a stable label like `assess-finding` and search by `label + title` before creating. Open OR closed match → reuse.
- **Search-based** (Jira via JQL, Notion, Linear queries): search the tracker for items with the action title. Match → reuse.
- **Hierarchical** (Task Master): list the current tag's tasks; compare titles. Match → reuse.
- **Free-form** (Apple Notes, Google Docs, plain markdown files): no reliable structured dedup. In this case, list existing items the user can see, **show them, and ask before re-creating**: "I see 3 existing items that look like these. Re-create, skip, or update? "

In all cases, before creating: search first. If a match is found (open or closed in trackers that have state), reuse it. If a match was previously closed (the gap was once resolved but has re-emerged), flag this to the user in the chat output - don't silently re-open or duplicate.

**Task rotation across runs:** when an action drops out of the Top 3 between runs (e.g., a hotspot graduated, or a higher-priority issue emerged), don't auto-close the existing tracker task. Leave it pending. Mention the demotion in the new report's "Additional Opportunities" section so the user can decide if it's still worth doing. The user owns the close decision.

### 6d: Link the items back to the assessment

How the link is recorded depends on the tracker, but the goal is the same: someone reading the assessment PR / report can click through to the items, and someone reading an item can click back to the assessment.

- **GitHub PR + GitHub Issues** (the original flow): edit the PR body so the `Issue` column in the Top 3 Actions table replaces `—` with `#N` references. Update `.assess/assess-report.md` locally so the on-disk report stays in sync (commit the change if you're working in a worktree before pushing).
- **GitHub PR + Task Master tasks**: include the task IDs in the `Issue` column (e.g. `TM #1.2`). Reference the assessment PR URL in each task's body.
- **GitHub PR + Jira**: include the Jira keys (e.g. `PROJ-1234`) in the `Issue` column. Set the assessment PR URL as a Jira link.
- **No PR was opened**: update only `.assess/assess-report.md` locally with the item references.
- **Other trackers**: do whatever makes the cross-link work; if the tracker doesn't support links back, include the assessment date + PR URL (if any) in each item's body so a human can trace it.

### 6e: Report back to the user

End with a short, tracker-specific summary. Examples:

> Created 3 GitHub issues: #42 (Action 1), #43 (Action 2), #44 (Action 3). Linked from the assessment PR. All labelled `assess-finding` so re-running `/assess` later won't duplicate them.

> Created 3 Task Master tasks under tag `assess-2026-05-22`: #1.1, #1.2, #1.3. Run `task-master next` to start.

> Action 1 already tracked in PROJ-1024 (in progress) - linked. Created PROJ-1198 (Action 2) and PROJ-1199 (Action 3) in Jira.

## Step 7.5: Finalize the wiki (required)

After writing `assess-report.md`, write `finalize-input.json` and invoke `assess_finalize.py` so the wiki files reflect the score and actions you chose.

````bash
cat > "$REPO_ROOT/.assess/finalize-input.json" <<'EOF'
{
  "score": 6.0,
  "maturity_label": "Solid",
  "top_action": "Add cyclop rule (threshold 15) to .golangci.yml",
  "hotspot_actions": {
    "src/foo.go": [
      "Split parseLine into smaller functions",
      "Add a test file at src/foo_test.go"
    ]
  }
}
EOF

<!-- chat-replace:uv-finalize -->
uv run "$SKILL_DIR/scripts/assess_finalize.py" "$REPO_ROOT"
````

This replaces:
- `log.md`'s last entry placeholder `**AI Readiness:** 0.0 / 7 ((LLM fills in))` with your actual score and maturity label.
- `log.md`'s last entry placeholder `**Top action:** Deterministic ranker not yet wired ...` with your actual Top 1 action.
- Each `hotspots/<slug>.md`'s `Suggested actions` section with the actions you derived for that file.

Without this step, the wiki carries deterministic-core placeholders forward forever. The hotspot page briefings will say "Pending LLM-generated suggestions" indefinitely.

The hotspot_actions dict should include at minimum the files mentioned in your Top 3 Actions. You can include more if you have specific suggestions for them.

## Step 7: Tool Feedback (Optional)

Close the loop: surface detected anomalies and offer the user a chance to file feedback against the toolkit.

```bash
jq '.anomalies' "$REPO_ROOT/.assess/run-context.json"
```

If the array is non-empty, list each anomaly to the user:

> Detected anomalies in this run:
> - `<code>`: <description>
>
> These may indicate a bug or miscalibration in `/assess`. Want to file feedback so the toolkit can improve?

Always also offer the open-ended option, even when no anomalies were detected:

> Anything else in this report look wrong or surprising? Filing feedback helps `/assess` improve for everyone.

If the user wants to file feedback, build a sanitized issue body from `run-context.json`:

- **Include**: plugin version, run date, files_scored, instructions_grade (top-level) + per-file subscores (numbers only - file basenames like `CLAUDE.md` are public), stats percentiles (p50/p95/max for LOC and CCN), diff summary counts, anomaly codes.
- **Never include**: file paths, code snippets, repo name, commit messages, hotspot path lists.

Prepend the body with: `_This feedback was generated by /assess. The data below is sanitized - no file paths or code content._`

Show the body to the user, then run (after explicit confirmation, per the never-auto-create-issues rule):

```bash
gh issue create \
  --repo bjcoombs/ai-native-toolkit \
  --label assess-feedback \
  --title "[assess-feedback] <user's summary>" \
  --body "$BODY"
```

The user adds their observation in their own words; the pre-fill is just the deterministic context. Positive framing applies here too: "the grader missed positive directives in section X" beats "the grader is broken."
