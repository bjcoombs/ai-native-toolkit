---
name: assess
description: "Assess a codebase's readiness for AI agent contributors against its maturity model of practices and autonomy Steps, and generate a complexity hotspot SVG treemap (size = LOC, hue = cyclomatic complexity, saturation = recent git churn). TRIGGER when the user types /assess, asks for an AI-readiness review, wants a complexity heatmap or hotspot map, asks 'how complex is this code?', wants migration risk triage, or asks for a codebase snapshot/report. Produces an MD report + SVG that can be opened as a PR in the target repo."
---

# AI Readiness Assessment + Complexity Hotspot

Three artefacts in one pass against a target repo:

1. **Practice assessment** - a deterministic headline (the autonomy Step, the gate that blocks the next one, "Catalogue coverage: N of M measured" and the per-tag strip) over per-practice verdicts across navigability, runtime liveness, code design, linters, architecture tests, CI, coverage, review bots, and AI project management.
2. **Complexity hotspot SVG** - Codecov-style treemap of the code. Size = LOC. Colour = cyclomatic complexity. Saturation = recent git churn. Vivid red = complex AND active = riskiest to change.
3. **Doc navigability SVG** - a node-graph of the docs. Structure = connectivity (centre = entry, rim = unreachable, dashed ring = orphan, solid edge = link, dotted edge = reference); colour = staleness (vivid red = a frozen doc beside churning code = a *lying map*); size = file length. Folds navigability and the decaying-map signal into one artifact.

Both SVGs are colour-blind-safe by default (OrRd ramp, no red-green).

All land as files inside the target repo. The skill always writes them locally; after writing, **ask the user** whether to open a PR in the target repo with the artefacts.

## The model: truth-pressure, not presence

Read this before scoring - it changes how you score. Across every layer, the real signal is never **presence**. It is whether a thing is under **active pressure to stay true**:

- Tests keep **behaviour** honest (CI fails when it's wrong).
- Retros / feedback loops keep the **process** honest (Layer 8 scores whether retros are *carried out*, not merely present).
- Maintenance keeps **docs** honest (a wiki tracked against code churn).
- Telemetry / liveness keeps **relevance** honest (is this code actually exercised).

So **AI-readiness is the degree to which a codebase's self-descriptions are kept honest, not the degree to which scaffolding exists.** Score artefacts on *maintenance pressure*, not existence. A stale-but-present doc scores **at or below absent**: missing makes the agent go look; confidently-stale makes it navigate fast to a wrong, current-looking conclusion.

The 9 layers (0-8) fall into three bands, ordered by dependency - what must hold for the next band to mean anything:

- **Read-side foundation** (L0 navigability, L1 liveness) - can the agent form a *true picture* before it acts?
- **Write-side enforcement** (L2-L7) - can the agent be trusted to produce good output? Only means something once you can trust that what you're reading is real and current.
- **Meta** (L8 feedback) - does the system keep itself honest over time? Depends on a working enforced system to improve, so it stays last.

### The three write-side tendencies the layers guard against

The write-side scores aren't abstract good practice - each traces to a known tendency of an AI contributor, observed across models. All three are the same defect: a self-description (the file's shape, a comment's promise, a gate's verdict) under no pressure to stay true. The deterministic core turns each into a cross-practice finding so the report names the specific files, not just the category:

- **Accretion** - an agent does what is asked, and what is asked is feature after feature; nothing in that loop asks for a refactor, so files only grow. **Now fully instrumented** via the `accretion_ratchet` finding: a file whose accumulated line count ratcheted monotonically upward across multiple commits with almost no deletion pressure (deletions below ~15% of total churn). Only top complexity/size-band files are flagged, never documentation, so growth-but-simple is never noise; `archive/`, `archived/` and `attic/` paths stay out of the attention list, disclosed in `excluded_as_archive`. It surfaces on three surfaces - the `accretion_ratchet` block in `run-context.json`, the `accretion_ratchet` cross-practice finding (with its files in the attention list), and a *growth-profile* line on each flagged hotspot page (`hotspots/*.md`). The signal disclaims itself (rather than dropping the result) when the git history is degenerate - a shallow clone or squashed import has no meaningful net-delta sequence, so the block carries `reliable: false` and the hotspot line is marked as possibly incomplete.
- **Unactioned intent** - an agent records promises it never returns to keep (`TODO` / `FIXME` / "remove after migration"). Instrumented via the `unactioned_intent` finding: markers aged by the edits they survived without being kept - a lying map of intent.
- **Guardrail erosion** - under pressure to make red go green, an agent loosens the check instead of fixing the root (a suppression, a skipped test, a widened threshold), hollowing out the layers meant to protect it while they still read as Present.

### Repository archetype (not every repo is software)

The layered model assumes a software repo. A **knowledge / document base** - markdown sources, an LLM-maintained wiki, a `CLAUDE.md` schema, and no application code or runtime - has no code surface for the write-side layers (L2-L7). Marking them Missing is itself a lying verdict: a well-run KB reads as mostly missing when it is in fact well-run, with gaps reported for tests on code it doesn't contain.

The deterministic core (`lib/keyhole/archetype.py`) classifies the repo and writes an `archetype` block to `run-context.json`:

- **Detection** is a heuristic - the code-file ratio (code vs markdown) and the absence of a runtime surface (`package.json`, `pyproject.toml`, `go.mod`, `Dockerfile`, ...). A documentation-heavy *application* (lots of markdown but a real build) stays software because of the runtime-surface gate.
- **Override marker.** An `assess-archetype: knowledge-base` (or `software`) marker in any instruction file (`CLAUDE.md`/`AGENTS.md`/...) **forces or suppresses** detection, so a maintainer is never trapped by a misfire. Write it as an HTML comment, e.g. `<!-- assess-archetype: knowledge-base -->`.
- **Scoring.** For a detected knowledge base the write-side layers (2-7) are marked **N/A** (not Missing) in the scorecard, so they name no gap; the applicable layers (L0, L1, L8) get verdicts on their merits. The headline is unchanged by the archetype: it reads the maturity model's conditions, not layers. A software repo gets a verdict for every layer 0-8.
- **KB-maintenance signal.** `archetype.kb_maintenance` flags whether the repo documents *how the AI maintains the KB* - the [Karpathy LLM-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (immutable raw sources, the schema file as the product, an ingest workflow, query-as-filing, periodic lint/consolidation). It is both a detection signal and a scored read-side (Layer 0) quality signal; the gist is cited in the report as the best-practice pointer whether or not the workflow is documented.

This is intentionally **one** archetype (knowledge base), structured as an extensible dispatch so more are cheap to add later - not a general archetype framework (YAGNI). The `assess-layer-scorer` agent reads the block (its Step 0) and the `assess-findings` skill renders N/A layers in the scorecard.

<!-- chat-replace:tour-arguments -->
Arguments: **$ARGUMENTS**. **`--tour` (the guided first run):** when the arguments contain `--tour`, skip every step below. Do not assess the current repository. Follow `references/tour.md` (relative to this skill dir) instead: it builds a small sample repository in a temporary directory, walks the report one section at a time, has the newcomer make one fix and re-runs so they see the Step move.


## Step 1: Determine Repo Root and Output Directory

```bash
git rev-parse --show-toplevel   # from the arg path if given, else pwd
```

Set `$REPO_ROOT` to the result. All scanning happens from here. **`--fix` and `--starter` (#1154)** are the only arguments that write starter files into the target (`/fleet` never does): when the arguments contain either, follow `references/fix.md` (relative to this skill dir); they commit to a local `assess/fix-<gate>` branch and never push, and only an added `--pr`, which an interactive run asks the user about first, pushes and opens a pull request.

**Scoping a subtree (`/assess <path>`).** When the argument is a directory under the repo root, scope the whole run to it - metrics, score, badge, wiki, and gate all computed for and labelled with the scope, artifacts under `.assess/<scope-slug>/`, no signal from a sibling. Pass `--scope "$SCOPE"` to `complexity-treemap.py`, `assess_core.py` and `assess_finalize.py` and swap `.assess/` for `.assess/<slug>/` throughout. Full recipe: `references/monorepo-scoping.md` (relative to this skill dir). A no-path run is whole-repo, unchanged. **Choosing the bar.** The run scores against the default maturity model (`levels/default.toml`) unless the user names another. `--profile handover` is the bar a production team expects before it takes a repository over; `--profile citizen` is the lighter bar while a citizen developer builds; each profile file (`levels/profiles/<name>.toml`) says why it holds each practice. `--levels FILE` takes a custom maturity model, as `/fleet --levels` does, and gives the same Step for the same repository. Pass the same flag to `assess_core.py` and to `--opt-in-mutation`. A profile or `--levels` run runs only the scans its model reads, plus those the headline needs, so it is quicker; every other block in `run-context.json` reads `not run under profile <name>` (the `scan_plan` block lists what ran and what did not), and the report says so in place of those sections. Never read such a block as zero or as unmet. Add `--all-scans` when the user wants the whole report under a profile. The executive summary then opens by naming the model and saying either "Ready for handover (profile: handover)" or exactly which practices are missing; practices the default model does not define are marked. Paste it as it stands. A profile or `--levels` run labels the badge and the `log.md` readiness line with the model (`... (profile: handover)`). A profile run keeps its own record in the committed `.assess/prior-level.json` beside the default model's, so "since last run" and the CI gate compare it with the last run of the same profile; a custom `--levels` run keeps none. When the user wants a practice of their own, `scripts/levels_new.py "$REPO_ROOT/my-team.toml" --profile handover` (the script is relative to this skill dir; run it with `uv run`, and write the copy into the user's repository, never the skill dir; leave out `--profile` to copy the default) writes a commented copy that loads with `--levels`; the toolkit's `docs/build-your-own-practice.md` walks through adding one.

Decide the output directory (default: `$REPO_ROOT/.assess/`). Create it if needed:

```bash
mkdir -p "$REPO_ROOT/.assess"
```

Artefacts will land at:
- `$REPO_ROOT/.assess/complexity-heatmap.svg`
- `$REPO_ROOT/.assess/complexity-stats.json`
- `$REPO_ROOT/.assess/doc-graph.svg`
- `$REPO_ROOT/.assess/step-ladder.svg` (written by the deterministic core from the level result: the maturity model's Steps as rungs, bottom to top, each with what an agent can do there, the gate between each pair, "You are here" on the run's Step and the next gate highlighted, "One change away" on the Step that one change reaches when that is past the run's next Step, and every gate already held above the run's Step marked "already in place"; the report shows it first), `$REPO_ROOT/.assess/evidence-waffle.svg` (also by the deterministic core from the level result: one square per maturity-model condition, coloured by state and evidence grade, a "Level N" header per column and the Step-gate squares outlined) and `$REPO_ROOT/.assess/tag-profile.svg` (from the level result's per-tag rows: one row per truth tag with the share of its measured conditions met, the earlier run's share as a hollow dot when `prior-level.json` holds the same maturity-model version)
- `$REPO_ROOT/.assess/package-map.svg`, `$REPO_ROOT/.assess/doc-drift.svg` and `$REPO_ROOT/.assess/review-strip.svg` (written by the deterministic core when their data is available: the 20 largest packages as an import matrix with front-door bypasses outlined, the docs furthest behind their code, and the review state of each sampled merged change)
- `$REPO_ROOT/.assess/assess-report.md`

> **Write-protected repo root?** `/assess` writes `.assess/` into `$REPO_ROOT`, and the treemap/core run as `uv` subprocesses that write there too. If your workflow keeps the repo root pristine and read-only (e.g. a `<repo>-main` clone that teammates branch from, with a hook blocking direct edits), a guard on *your* writes won't stop the subprocess - it just makes the run write into the directory you meant to protect. **Create a worktree first and run `/assess` from there.**

## Step 2: Generate the Code Heatmap + Doc Graph

This step produces **two** views of the codebase, both colour-blind-safe (OrRd ramp, no red-green):

- **Complexity heatmap** (`complexity-heatmap.svg`) - a treemap of the *code*. Size = LOC, colour = cyclomatic complexity, saturation = recent churn. Vivid red = complex AND active = "hard to change safely".
- **Doc navigability graph** (`doc-graph.svg`) - a node-graph of the *docs*. Structure shows connectivity (centre = entry point, rings = link-distance, rim = unreachable; orphans carry a dashed ring; solid edges are links, dotted edges references); colour shows staleness in the same grammar as the code heatmap (vivid red = a frozen doc beside churning code = a lying map); size = file length. It folds both Layer 0 doc signals - navigability and the decaying-map - into one artifact. Beyond static wikilinks and CommonMark links, it counts a backticked path to an existing doc as a reference edge (a cited `.claude/` file included) and recognises Obsidian vault-native navigation - `.base` view hubs and `dataview` query blocks - as edges (resolved statically by folder / tag / frontmatter predicate), so a vault navigated by dynamic queries isn't mis-scored as orphaned. The SVG and the scored signal compute over the identical doc set: both honour the same excludes (`.assess/config.toml`).

Feed the complexity stats into the linter/complexity layer (Layer 3) and the `doc_graph` / `doc_staleness` blocks of `run-context.json` into **Layer 0** (the graph SVG is the visual; the score reads the structured blocks). The core also reads the repo's Claude Code configuration - `.claude/skills/`, `.claude/commands/`, `.claude/agents/`, and for a plugin repo `skills/`, `commands/`, `agents/` or the paths `.claude-plugin/plugin.json` names - and writes the `claude_config` block: legacy command files, and frontmatter keys or values Claude Code silently ignores, checked against documented field sets pinned in `scripts/lib/instructions/claude_config_fields.py` with their snapshot date. It is Layer 0 evidence, not a badge finding; `available: false` (no `.claude/` and no plugin manifest) means not checked, never clean. Beside it, the `agent_harness` block lists what the committed agent configuration runs on every session (settings and frontmatter hooks, `env` names, `apiKeyHelper`, `.mcp.json`, `.cursor/hooks.json`) and flags a tracked `.env` or `settings.local.json`, a missing `.env` deny rule, `disableAllHooks: false`, a broad `Bash` allow, a devcontainer credential mount, hidden Unicode in instruction files and comment-triggered deploys; it is informational and never prints a secret value. The `instruction_placement` block splits each instruction file on its headings and names the sections that belong outside the always-loaded layer (line ranges, the signals that fired, a suggested home), with agent and `SKILL.md` files over 200 lines in a separate informational list. It is Layer 0 advice; the instruction grade is scored on the same split (always-on lines past 200, and lines that belong elsewhere), and the agent and skill list has no grade effect. The `agent_environment` block feeds **Layer 5**: committed agent setup (`copilot-setup-steps.yml`, a dev container, `.cursor/environment.json`, a `SessionStart` hook, a setup script or target), the check command an instruction file names, lockfiles per manifest, and `layer5_cap`; its `ci_duration` is the one `gh` read, optional and `available: false` with a reason when `gh` cannot answer. The `ai_usage` block is informational and scores no layer: the share of commits in the churn window (the `--window` in a headless run) with an AI-assistant `Co-authored-by` trailer, the share authored by an agent identity (pinned agent apps plus the `[ai_usage]` rules of `.assess/config.toml` or `--fleet-config`), and the share with either, each a lower bound on agent use (squash merges, missing trailers and unrecognised identities undercount), so report it as "at least", never as an estimate. Dependency bots and GitLab access-token users are counted apart, never as agents unless a configured rule says so. The `agent_outcome` block (#1085) is informational too and scores nothing: of the agent commits `ai_usage` counts (trailer or agent author), the share later reverted (a `This reverts commit` message naming it, or a later inverse diff) and the share with an added line rewritten within `rework_days` (14), with the counts and the window; a share with nothing to divide by is null with a reason, and a shallow clone makes the block unavailable. **Bot and agent accounts (#765).** Only unambiguous agent accounts (`claude[bot]`, `noreply@anthropic.com`) and AI co-author trailers count unconfirmed; every other automated-looking account counts as nothing and is counted in `ai_usage.identities_awaiting_confirmation`. The human classifies them in a committed `[identities]` section of `.assess/config.toml` of literal e-mails or logins (`agents = ["cursor@example.com"]`, `bots = ["codecov[bot]", "renovate[bot]"]`, `people = ["ann@example.com"]`); a confirmed class beats every pinned agent rule (a dependency bot stays a dependency bot). The first-run discovery is `uv run scripts/identity_candidates.py <repo>` (terminal only, never prompts; `--json`; `--confirm agent=ACCOUNT`, `bot=` or `person=` writes the answer): after the core, list the candidates, ask the human in an interactive run, record the answers and re-run the core, following `references/identities.md`; a headless run never asks and the report states "N accounts awaiting confirmation" as a next step.

### The consent lifecycle (read `references/consent-lifecycle.md`)

Steps 2a/2b/2d and the assess-pr end-of-run offers share one consent model, **specified in full in `references/consent-lifecycle.md`** (relative to this skill dir) - load it before running the offers. Essential hooks the steps rely on: decline markers carry provenance (write `.no-<tool>` JSON via the reference's `write_decline_marker` helper, never a bare `touch`; a mutation decline under an older plugin *major* sets `reoffer_mutation: true` so Step 2d re-asks once); three phases each a single batched question (Phase 1 tool installs 2a+2b, Phase 3 the separate mutation pass 2d, Phase 2 the assess-pr write-back offers); and the non-interactive contract - a headless/CI run makes **no AskUserQuestion calls in any phase**, Phase 1 as orchestration from your runtime context and Phases 2/3 from the core's `run-context.json .interactive` flag, which pre-records every skipped offer in `.offers`.

### 2a: Detect `scc` need (feeds Phase 1)

The bundled treemap uses [`lizard`](https://github.com/terryyin/lizard) (Python, Go, JS, Java, C/C++, etc.) by default. Optional `scc` extends coverage to 200+ languages including markdown, JSON, YAML, SQL, and shell - useful when the repo's surface is more than just traditional source code.

Before scanning, check three signals:

```bash
# 1. Is scc already on PATH?
command -v scc >/dev/null 2>&1 && SCC_PRESENT=1 || SCC_PRESENT=0

# 2. Has the user previously declined for this repo?
[ -f "$REPO_ROOT/.assess/.no-scc" ] && SCC_DECLINED=1 || SCC_DECLINED=0

# 3. Is the repo mostly markdown/data/config (where lizard alone will be sparse)?
#    Cheap heuristic: count non-code files vs code files. The `.` argument is
#    the regex pattern (matches every path) and "$REPO_ROOT" is the search
#    path - without `.`, fd treats $REPO_ROOT as the pattern itself, matches
#    nothing, and silently returns 0.
CODE_FILES=$(fd -t f -e py -e js -e ts -e tsx -e jsx -e go -e java -e kt -e rs -e rb -e cs -e swift -e dart -e cpp -e c -e h -e php . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
NONCODE_FILES=$(fd -t f -e md -e json -e yaml -e yml -e toml -e sh -e sql . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
```

**Add `scc` to the Phase 1 offer list only if all three are true:** `SCC_PRESENT=0`, `SCC_DECLINED=0`, and the repo looks lizard-sparse (`CODE_FILES < NONCODE_FILES` or `CODE_FILES < 10`). Otherwise it contributes nothing to Phase 1. Do **not** ask here - `scc` is batched with the dead-code tools into the single Phase 1 question in Step 2b. Its trade-off phrasing, for when the batched question is presented:

> "This repo has <N> code files and <M> non-code files (markdown/JSON/YAML). `scc` would include the non-code files in the treemap; without it the treemap may be sparse. Install `scc`?"

The three options are the shared Phase 1 shape (**Install** / **Skip for now** / **Skip permanently** via `write_decline_marker scc`). If the user accepts, run the platform-appropriate command (do **not** auto-install - `brew install` is a system mutation):

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

If the install fails or the platform isn't covered, fall back to lizard-only and continue - don't block the assessment.

### 2b: Phase 1 - batched analysis-tool install offer (capability-driven, detect-or-propose)

`/assess` maps each Layer 1/Layer 3 analysis **capability** (liveness/dead-code, static module graph, linting, modernization) to a serving tool. Historically that map was a **hardcoded per-language allowlist** - `vulture` for Python, `ts-prune`/`knip` for TS/JS, `staticcheck`/`deadcode` for Go. The defect that allowlist created: when a repo's language **isn't enumerated**, every capability silently degraded to "unavailable" - the report read "this layer is absent here" rather than "a tool could serve this - install one?". A non-enumerated language was locked out with no resolution path inside the run.

The flow is now **capability-driven detect-or-propose**, in three moves per capability:

1. **Detect** whether a serving tool already exists (on PATH, or configured in build/lint config). If it does, **use it** - and if it's configured in the build, **credit it; never re-offer**.
2. **Propose** an ecosystem-appropriate candidate when none exists. For an enumerated language this is the table below; for a non-enumerated one **you propose a fitting tool at runtime** (reasoned latitude - you are not locked out because the language isn't in a hardcoded list). Ask the user with the same **AskUserQuestion** pattern.
3. **Honest-degrade** anything you can detect-but-not-serve: name the capability **and** a candidate tool in the report. This is a deliverable state distinct from both "Present" and a silent "Missing" - never let a capability vanish without naming what would serve it.

The per-language dead-code offer below is the simplest instance (one capability, install-consent). When the tool is absent, the scan degrades to `tool_absent` and the user has no resolution path inside the skill - they'd have to know which tool fits the language, which package manager to use, and run the install themselves. The same install-offer pattern as Step 2a closes the loop without leaving them to figure it out.

Detect languages with cheap `fd` counts (mirroring Step 2a's heuristic - the treemap script's own classification isn't exposed in the stats sidecar, and shelling out is fine here):

```bash
PY_FILES=$(fd -t f -e py . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
TS_FILES=$(fd -t f -e ts -e tsx . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
GO_FILES=$(fd -t f -e go . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')

# Per-language candidate tool. Prefer the read-only tool first - `ts-prune` over
# `knip` for TS, `staticcheck` over `deadcode` for Go - so the user isn't asked
# twice for the same job and the chosen tool doesn't need to build the project.
needs_offer() {
  # Args: tool, file count, min; 0 = ask. Braced: skill-arg substitution skips them.
  local tool="${1}" count="${2}" min="${3:-5}"
  [ "$count" -ge "$min" ] || return 1
  command -v "$tool" >/dev/null 2>&1 && return 1     # already installed
  [ -f "$REPO_ROOT/.assess/.no-$tool" ] && return 1  # user declined permanently
  return 0
}

OFFERS=()  # each entry: "language|tool|install_cmd"
# Seed with scc first when Step 2a flagged it (the treemap-coverage tool shares
# this one batched question with the per-language dead-code tools).
[ "${SCC_PRESENT:-1}" = 0 ] && [ "${SCC_DECLINED:-0}" = 0 ] \
  && { [ "${CODE_FILES:-0}" -lt "${NONCODE_FILES:-0}" ] || [ "${CODE_FILES:-0}" -lt 10 ]; } \
  && OFFERS+=("coverage|scc|brew install scc (or apt/dnf/go install - see Step 2a)")
needs_offer vulture "$PY_FILES"      && OFFERS+=("python|vulture|pip install vulture (or 'uv tool install vulture')")
needs_offer ts-prune "$TS_FILES" && [ -f "$REPO_ROOT/tsconfig.json" ] && OFFERS+=("typescript|ts-prune|npm install -g ts-prune")
needs_offer staticcheck "$GO_FILES"  && OFFERS+=("go|staticcheck|go install honnef.co/go/tools/cmd/staticcheck@latest (or 'brew install staticcheck')")
```

If `OFFERS` is empty (no language hits the threshold, or every tool is already installed/declined), skip straight to 2c. **Non-interactive short-circuit:** Phase 1 precedes the core, so in a headless/CI run make **no** AskUserQuestion call and install nothing - proceed to 2c with lizard-only plus whatever is already on PATH (the core records the skip in `offers` at 2c).

Otherwise, in an interactive run, batch **all** of Phase 1 into **a single AskUserQuestion call** - one question per entry in `OFFERS` (scc and each dead-code tool together), three options per question. This is the one tool-install decision surface; the user never faces scc and the linters as separate modals:

- **Install <tool>** - run the cited install command and continue.
- **Skip for now** - proceed without the tool. Don't write a marker; ask again next run.
- **Skip permanently for this repo** - `write_decline_marker <tool>` so future runs don't ask. Recommended when the language only appears in scripts/configs that don't warrant symbol-level reachability.

Phrase each question so the gain is concrete, e.g.:

> "This repo has 47 Go files. `staticcheck -checks U1000` would let `/assess` flag unreachable Go funcs as Layer 1 candidates. Install? (`go install honnef.co/go/tools/cmd/staticcheck@latest` or `brew install staticcheck`)"

When the user picks **Install <tool>**, run the platform-appropriate command from the offer. Surface any install failure as a chat message and continue - dead-code tools are degrade-don't-block (same contract as scc); a missing tool reduces Layer 1's precision but never gates the assessment. When they pick **Skip permanently**, `write_decline_marker <tool>` (e.g. `write_decline_marker staticcheck`). The user answers this one batched question once and the run proceeds with whichever tools they accepted.

#### JVM / Maven capability offers (v1)

When the deterministic core detects a Maven or Gradle project (a build file plus at least one `.java`, `.kt`, `.scala` or `.groovy` file outside platform-wrapper `android/` directories, Cordova's `platforms/android/` included: a Flutter, React Native, Capacitor or Cordova shell is not a JVM codebase) it emits a `capability_offers` block in `run-context.json` - the first proof of the capability-driven flow on a non-enumerated ecosystem. Read it after Step 2c's core run, before scoring, and act on each capability's `state`:

```bash
jq '.capability_offers' "$REPO_ROOT/.assess/run-context.json"
```

- **`liveness` → `state: "offer"`** - Maven was detected but `mvn dependency:analyze` (coarse module-level dead-dependency detection) has not run. The `consent` field names the shape: `run` (`mvn` is on PATH - offer to **run** it against the project; `dependency:analyze` needs a *compiling build*, so this is a **run-consent**, heavier than a static scan) or `install` (`mvn` absent - offer to **install** Maven first). Use **AskUserQuestion** exactly as Step 2b, phrasing the trade-off (a build that resolves dependencies and may hit the network) - but honour the non-interactive contract: when `run-context.json .interactive` is `false`, skip this offer and honest-degrade the capability instead of prompting. On accept and a `run` consent, run `mvn dependency:analyze`, capture its output, and re-run the core with the served result so the candidates feed Layer 1. On decline, the capability stays honestly named, not silently dropped.
- **`linting` / `modernization` → `state: "credited"`** - an already-configured pom.xml plugin serves it (`served_by` lists which: Checkstyle, SpotBugs, PMD, error-prone, OpenRewrite, Modernizer). **Credit it in the report; do not re-offer.**
- **Any capability → `state: "honest_degrade"`** - nothing serves it yet (module graph, linting/modernization without a configured plugin, and **all** capabilities under Gradle in v1). The block carries a `candidate_tool` and `gloss`. **Name both in the report's Layer 1/Layer 3 prose** ("module-graph analysis is unserved here; `jdeps` would provide it"). Honest-degrade is a deliverable - surfacing the candidate is the point.

**Boundary (v1).** Only Maven liveness is *served*. Module graph (`jdeps`), linting, and modernization honest-degrade; Gradle honest-degrades entirely. The `candidate_tool` values are deterministic defaults - you may propose a better-fitting ecosystem tool at runtime (the detect-or-propose latitude above); that choice is human-judged, not CI-tested. CI tests only **signal consumption**: given a tool's output, the scorecard feeds correctly.

### 2c: Run the treemap

Run the bundled treemap script alongside the deterministic core - see the chained block below.

The script prints a one-line summary (file count, lizard vs scc coverage, churn window chosen, top 5 biggest files). The stats sidecar contains percentiles (p50/p95/max for LOC, CCN, churn) and ranked lists of the top 10 files by hotspot score, raw CCN, and raw LOC, plus every scored file's CCN in an uncapped `files` list. Both feed the report. A file scc scored at file level only (`source` `scc`, no `max_fn_ccn`) carries a keyword count, not a parsed complexity: `assess_report.py` renders it as `~N (approximate)`, or `~N (unreliable)` when `complexity_unreliable` lists the path because the count is implausibly low for the file's size.

**Dependencies:** the script uses PEP 723 inline metadata (`lizard`, `squarify`, `matplotlib`, `numpy`). `uv` resolves them on first run.

**Build artifacts, generated test reports and generated code are filtered by default** (full list in `complexity-treemap.py`'s `EXCLUDE_DIRS`, `EXCLUDE_FILE_PATTERNS` and `EXCLUDE_NESTED_PATH_PATTERNS`; pass `--include-artifacts` to score them, e.g. to visualise how much of the repo is generated). The script excludes three classes of files:

- **Build artifacts**: `main.dart.js`, Flutter canvaskit/skwasm runtime bundles (`canvaskit.js`, `skwasm*.js`), `*.min.js`, `*.bundle.js`, `*.chunk.js`, `*.map`, sourcemaps, service workers, and files under `node_modules/`, `dist/`, `build/`, `.next/`, `.nuxt/`, `.output/`, `coverage/`, etc.
- **Generated test reports**: `html-report/`, `playwright-report/`, `lighthouse-report.html`, `lighthouse-results.json`, `zap-report.*`, and `*.jsonl` under a `fixtures/` directory below the top level. When the 5 largest files are all JSON, YAML or JSONL scored by scc with complexity 0, stderr hints at `.assess/config.toml` excludes.
- **Generated code**: protobuf bindings (`*.pb.go`, `*_grpc.pb.go`, `*.pb.gw.go`, `*.connect.go`, `*_pb.ts`, `*_pb.d.ts`, `*_pb2.py`, `*.pb.cc`, `*.pb.h`), Go generators (`*.gen.go`, `wire_gen.go`, `zz_generated_*.go`, `bindata.go`), .NET source generators (`*.designer.cs`, `*.g.cs`), Dart/Flutter codegen (`*.freezed.dart`, `*.g.dart`, `*.gr.dart`), `*.generated.*`, `*.gen.ts`, `database.types.ts`, and any file with a comment in its first 5 lines carrying a generator marker (`DO NOT EDIT`, `@generated`, etc.; reason `generated-header`) or whose average line exceeds 1,000 characters (reason `long-lines`). Content-matched files are listed in `excluded_generated` (stats file and `run-context.json`), which the report and gate disclose.

**Dominance warning.** If a single file still holds >30% of total scoreable LOC after filtering (the threshold compiled bundles typically cross), the script prints a warning to stderr identifying the file. When you see this, the right next step depends on *why* the file is large:

- **Compiled bundle or committed build output** (`main.dart.js`, a bundled JS file, etc.): surface in the report's "Hotspot snapshot" section as "`<file>` holds X% of LOC and is likely a build artifact - recommend adding to `.gitignore` and re-running." Add a Top 3 Action of the same shape.
- **Intentionally-tracked reference data** (regulatory raw exports, vetted-context corpora, seed datasets, large CSV/JSON reference tables): the file is *meant* to be in git but isn't source code. The fix is `--exclude`, not `.gitignore`. Recommend the user persist the rule in `.assess/config.toml` so subsequent runs apply it automatically (see "Custom excludes" below). Do not push toward `.gitignore` in this case.
- Either way, do NOT skip the rest of the assessment - the layered scan still produces useful signal.

**Custom excludes for vetted-context / reference data.** When the repo intentionally tracks large non-source files, two mechanisms extend the built-in defaults (the built-ins always apply; both layers are additive). **The same excludes apply across every scan** - the heatmap, the doc-navigability graph, the doc-staleness pass, and the liveness scan all honour the same list, so "this is reference data, not source" is a single statement, not a per-layer toggle:

1. **CLI flag** `--exclude PATTERN` (repeatable, ad-hoc). A plain string is matched as a directory name; a glob is matched against the basename. The flag exists on the treemap script for one-off runs:

   ```bash
   <!-- chat-replace:treemap-exclude-example -->
   uv run "${CLAUDE_SKILL_DIR}/scripts/complexity-treemap.py" "$REPO_ROOT" --exclude regulatory-raw --exclude vetted-context --exclude '*.csv'
   ```

2. **Per-repo config** `.assess/config.toml` (durable, version-controllable, applies to **every** scan via the orchestrator). Recommended for any exclude the user will want to apply every run:

   ```toml
   exclude_dirs = ["regulatory-raw", "vetted-context", "seed-data"]
   exclude_patterns = ["*.csv", "*.parquet"]
   ```

   No section header is needed - the file is already namespaced by living under `.assess/`. Missing or malformed files degrade silently to no extra excludes; the assessment never blocks on a broken config.

**Provenance for generated docs (staleness measured against the source).** A *generated* doc - a Jira note dump, an API reference, codegen output - is not stale because the file is old; it is stale when the **source it was derived from has moved on**. The mtime/age model gets this backwards: a freshly regenerated dump of 1,200 notes shares one recent mtime (looks fresh) even when its source changed afterwards, and an old-but-accurate generated doc reads as a lying map when it is not. Declare provenance and doc-staleness is computed as "is the source newer than the doc?" instead - a generated doc whose source is quiet is never flagged as a `lying_map`, regardless of how busy the surrounding code is. Two ways to declare it (frontmatter wins when both name a source for the same doc):

1. **Frontmatter** on the generated doc - a `source:` key (a string or a list), resolved relative to the repo root first, then to the doc's own directory. An optional `generated_by:` records the generator for humans (it does not affect staleness):

   ```markdown
   ---
   source: data/jira.tsv
   generated_by: scripts/dump-jira-notes.py
   ---
   ```

2. **Per-repo config** `.assess/config.toml` `[[generated]]` array-of-tables, for bulk-generated trees that cannot each carry frontmatter. `path` is a folder relative to the repo root; every doc under it inherits the mapping. `source` is a string or list of strings relative to the repo root:

   ```toml
   [[generated]]
   path = "notes"
   source = "data/jira.tsv"
   ```

   When a generated doc's source is newer than the doc, the staleness verdict is a direct, high-confidence source-vs-doc comparison (git commit time, falling back to mtime) - so a stale generated doc over complex code still surfaces as a `lying_map`, while a fresh one never does.

**Cross-repo mappings (opt-in).** A central docs repository whose docs describe other services can declare each mapping in `.assess/config.toml`; `scope` (the documented surface inside the service) is required, and an entry without it is rejected with a message saying so:

```toml
[[describes]]
path = "services/billing.md"   # a doc or folder in this repository
repo = "grp/billing"           # a repository id in the fleet
scope = ["api/"]               # or ["openapi.yaml"], ["proto/"]; ["."] = whole repo, low confidence
```

A standalone `/assess` lists them under `doc_staleness.declared_cross_repo` as declared, not measured outside fleet mode; only `/fleet`, which holds the service checkouts, measures them.

The script's own output directory `.assess/` is excluded automatically - prior runs' `run-context.json` and SVGs never feed the next run's heatmap, the doc graph, or the dead-code scan. Test fixtures under `**/tests/fixtures/**` are likewise excluded automatically - they are inputs that exercise the scanners (sample `CLAUDE.md` / monolithic-instruction files), not navigational docs or live code, so counting them would inflate the orphan rate and depress the Layer 0 navigability read.

**Raw-source-tree exclusion.** The read-side metrics (orphan rate, reachability, broken links) describe the **curated wiki** - the navigable layer an agent traverses. A repo can also track trees of raw, machine-extracted source documents (a disclosure / SAR export of hundreds of `.msg`/`.pdf`/`.docx` files converted to markdown). Those are immutable raw sources: they legitimately have no inbound wiki links and carry machine-extracted, non-navigational links (`mailto:`/`tel:`/footer URLs), so counting them as orphans / broken links inflates the figures and masks the curated signal. The doc graph auto-detects such subtrees - threshold-based: a large subtree that is almost entirely link-isolated *and* carries the machine-extraction fingerprint (`lib/complexity/raw_source.py`) - and **excludes** them from the headline metrics, reporting each excluded tree + its file count (`doc_graph.excluded_raw_trees`) and the raw layer's own figures separately (`raw_source_doc_count` / `raw_source_orphan_rate` / `raw_source_broken_links`). A second fingerprint excludes working-notes trees the same way: pattern-named notes (plans, session logs, tickets) mostly linked once from one or two index files, reported as `excluded_working_notes_trees` / `working_notes_doc_count`; in `.assess/config.toml`, directories relative to the repo root (a prefix, not a name matched anywhere like `exclude_dirs`): `working_notes_dirs = ["journal"]` excludes one from the headline and `working_notes_ignore = ["docs/chapters"]` keeps one counted. A repo with neither tree is unaffected. The detection reuses the link graph already built, so there is no second parse.

**If the script fails** (no `uv`, no scoreable files, etc.), record the error in the report under "Hotspot snapshot" as "could not be generated - <reason>" and continue with the layered assessment. The treemap is additive; assessment still runs without it.

Run the full sequence - rotate the prior sidecar first, then the treemap, then the deterministic core:

```bash
# Rotate the prior stats sidecar so the diff has something to compare against next run
if [ -f "$REPO_ROOT/.assess/complexity-stats.json" ]; then
  cp "$REPO_ROOT/.assess/complexity-stats.json" "$REPO_ROOT/.assess/complexity-stats.prior.json" 2>/dev/null || true
fi

# Run the complexity treemap (produces fresh complexity-stats.json)
# (single line: the standalone transform replaces the marker + one following line)
<!-- chat-replace:uv-treemap -->
uv run "${CLAUDE_SKILL_DIR}/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json"

# Run the doc navigability graph (connectivity + staleness in one SVG; feeds Layer 0)
<!-- chat-replace:uv-doc-graph -->
uv run "${CLAUDE_SKILL_DIR}/scripts/doc-graph-svg.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/doc-graph.svg"

# Run the deterministic core (instruction grading, Claude Code config frontmatter,
# doc link-graph, doc staleness, liveness/dead-code, observability rungs, stats
# diff, wiki files, run-context.json)
# On a headless/CI run (as in Phase 1), append `--non-interactive` so every consent offer records as skipped; a normal interactive /assess omits the flag.
<!-- chat-replace:uv-core -->
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_core.py" "$REPO_ROOT"
```

Either SVG is additive: if a script fails (no `uv`, no scoreable files, no docs), record "could not be generated - <reason>" in the report and continue. The doc graph shares its data with the deterministic core's `doc_graph` / `doc_staleness` blocks, so even when the SVG can't render, Layer 0 still scores from `run-context.json`.

<!-- chat-skip:start -->
#### Windows (PowerShell, no uv)
```powershell
python -m pip install --user networkx grimp lizard==1.23.0 squarify matplotlib numpy  # once, no uv
$REPO_ROOT = git rev-parse --show-toplevel; python "${CLAUDE_SKILL_DIR}/scripts/assess_core.py" "$REPO_ROOT"
```
Run each other `uv run` line in this skill as `python` with the same arguments. The full PowerShell sequence and the ripgrep lookup (`RG_PATH`, then `PATH`, then the WinGet package folder): `references/windows.md`. Installs, PATH notes and the make-free commands: the [Windows setup guide](../../docs/windows.md).
<!-- chat-skip:end -->

Interactivity is an explicit signal, never a stdin probe: pass `--non-interactive` only on a headless/CI run, otherwise the run is interactive by default. See `references/consent-lifecycle.md` for why (`isatty()` misreads a subprocess). Managed settings are opt-in: append `--managed-settings` only when the user asks for this machine's Claude Code managed settings to count toward the agent permissions practice (a central deny rule then counts, graded listed), or set `managed_settings = true` under `[agent_ops]` in `.assess/config.toml`. Off, the practice reads the repository alone. Practice packs are opt-in too: append `--pack FILE` (repeatable) only when the user names a `<name>.pack.toml`; its checks are read from the repository's files, never run, and land under `pack.<name>.<check>`, which a `--levels` model passed on the same run may read. A pack's own `[[rows]]` are layered onto the model (the default, the profile or the `--levels` file) in `--pack` order, so a company pack and a team pack apply together; each `level.conditions[]` entry then names its `source`, and the executive summary's `Practice packs ...` line names the pack beside each of its practices. Paste it as it stands.

Now `$REPO_ROOT/.assess/run-context.json` contains the structured data you need for the prose sections. Read it before writing the report. **The level result** is the `level` block of `run-context.json`: the run's maturity model's conditions (the default, or the `--profile` / `--levels` one) (`<row id>.<level>`) each with its `state`, `grade` and a `reason` code when not measured, or `requires:<id>` when it is unmet or not measured because a condition it requires is not met; the verified tally `tally.met` (measured and verified conditions met; config-only and declared ones are only `listed`), `tally.measured` and `tally.not_measured`; the autonomy `step` and the `next_step` with its `blockers`. It is computed by the deterministic core (`scripts/lib/scoring/level_tally.py`), never by you, and `/fleet --levels` with the default model gives the same result on the same commit (its `level.json`); a caller's own model can read differently. The shape is `$defs.level_result` in the fleet skill's `references/level.schema.json`; the `scripts/lib/scoring/level_tally.py` module docstring documents every field.

**The headline** is the `headline` block of `run-context.json` (`scripts/lib/scoring/headline.py`), worded from the level result and rendered verbatim by the report. The report opens with its top card (`headline.summary_markdown`, `scripts/lib/top_card.py`), before any section heading, stating each fact once: the Step, the practices fully in place (the level result's `in_place`, the first three by title, strengths before any gate; the deterministic report's "In place" section gives each its why), the gates held with "N of M measured catalogue conditions met, V verified" (how much of the catalogue the run could read, never a progress figure), the gate that blocks the next Step (and a monorepo unit that already holds it), what moved since the last run, and one Start here and one Done when from the first open action of `actions.json`. `headline.lines` (printed under `## Catalogue Coverage`) holds two lines: `Catalogue coverage: K not measured (reasons) · model <version>` and the per-tag strip `By tag: behaviour 2 of 3 · docs 0 of 0 (NOT MEASURED: 1) · ...` (one cell per truth tag; a condition with several tags is in each cell and counted once in N; NOT MEASURED, HAZARD and DECLINED are badges). `headline.verified` and `headline.tags` hold the same numbers for counts-only output. The `log.md` entry opens with the `since_last_run` block, and the card states it in one line (`scripts/lib/since_last_run.py`): actions marked done in the committed `actions.json` whose `completed_sha` is on HEAD's history and landed after the commit the previous run measured, each with its commit, the gates gained or lost; a run with no committed baseline reads "This run is your baseline", and a previous state or an `actions.json` it cannot read, or one not committed, is "not known", never zero. A same-day re-run on the same commit keeps the baseline of the run it supersedes. Done `remove` actions are tracked by the pruning ledger (`scripts/lib/pruning_ledger.py`): after two runs on new commits with the file unflagged and no gate lost, the section credits "Pruned: always-on text in `<file>`, saved N always-on lines", the measured drop. Nothing is scored. The baseline lives in the committed `.assess/prior-level.json` (`baseline`: run id, measured commit, held gates), so commit it with `log.md` and `actions.json` for CI and teammates to see the credit. `headline.summary` (`Step 3 · 2 practices fully in place · next: coverage gate`, or `Step 4 · 5 practices fully in place · all gates held` at the top Step; the practices at their top level lead, left out at 0, and no tally count) is the `badge.json` message and the `log.md` readiness line. A Step reads its gates only (`references/scoring-model.md`), so a condition added to the catalogue never moves it. There is no layered score and no band name; you write the prose around the headline and never set it.

The `plugin_version` field in `run-context.json` tells you which plugin version produced this run. Surface it at the top of the report (e.g., "Generated by `/assess` v1.8.0") so readers can spot it if a stale cached version of the plugin produced unexpected output.

### 2d: Phase 3 - the bounded mutation pass (opt-in, kept separate)

**This is its own phase, asked on its own - never batched with the Phase 1 tool installs.** The default core run is read-only - it never mutates or runs code, so `test_pressure` carries the cheap hollow-test heuristics and mutation-config detection but no survivor data. The decisive Layer 1 signal (would a test actually fail if the code were wrong?) needs this bounded pass, which **modifies your source files and runs your test suite over the mutated code** - a different risk class, so it gets a dedicated question with explicit code-modification framing rather than being waved through inside a bundle of install prompts, and honours the non-interactive contract (skip when `run-context.json .interactive` is `false`).

The `test_focus` block is the single source of focus targets - the risky files that most need test work, already cross-joined from hotspot risk, coverage, and the hollow-test heuristics. Read it; don't recompute it:

```bash
jq '.test_focus' "$REPO_ROOT/.assess/run-context.json"
```

<!-- chat-replace:mutation-offer-intro -->
**Only when `test_focus.entries` holds at least one entry with test evidence** (`test_signal` of `covered_but_hollow` or `sibling_test_only`, on a hot file that is not itself a test) is there anything to deepen - mutating a file with no test yields all survivors and measures the missing test, not an existing one, so `unsupported` / `no_covering_test` / `unknown_no_coverage` entries stay in the report table but out of the mutation scope. If no entry qualifies, skip straight to Step 3. When it has entries, follow the same detect-or-offer-install pattern as Steps 2a/2b: first detect a mutation tool, then ask the user whether to run the bounded pass.

<!-- chat-skip:start -->
**Detect a mutation tool for the repo's language.** Mirror the Step 2b heuristic - `mutmut` for Python, `stryker` for TS/JS - and check PATH plus the permanent-decline marker:

```bash
# Scope: entries with test evidence, ranked, minus test files (the core's mutation_scope;
# regex mirrors sibling_tests.IS_TEST_RE; suffixes mirror test_focus.MUTABLE_SUFFIXES).
# Tool by dominant focus-file language
# (Python -> mutmut, TS/JS -> stryker); mutmut when mixed.
FOCUS_FILES=$(jq -r '(.test_focus.entries // [])[] | select(.test_signal == "covered_but_hollow" or .test_signal == "sibling_test_only") | .path | select(split("/") as $p | (($p[-1] | sub("\\.[^.]*$"; "") | test("(^test_|_test$|\\.test$|\\.spec$|_spec$|Tests?$)")) or ($p[-1] | endswith(".bats")) or any($p[:-1][]; . == "__tests__")) | not) | select(test("\\.(py|tsx?|jsx?|go|rs)$"))' "$REPO_ROOT/.assess/run-context.json" | head -5)
case "$FOCUS_FILES" in
  *.ts|*.tsx|*.js|*.jsx) MUT_TOOL=stryker ;;
  *) MUT_TOOL=mutmut ;;
esac
command -v "$MUT_TOOL" >/dev/null 2>&1 && MUT_PRESENT=1 || MUT_PRESENT=0
[ -f "$REPO_ROOT/.assess/.no-$MUT_TOOL" ] && MUT_DECLINED=1 || MUT_DECLINED=0

# Re-offer once when the existing decline was made under an older plugin major
# (the mutation pass may have changed materially since). The core computes this.
REOFFER_MUT=$(jq -r '.reoffer_mutation // false' "$REPO_ROOT/.assess/run-context.json" 2>/dev/null)
```

**Offer with AskUserQuestion** as a **standalone question** (skip it entirely when `run-context.json .interactive` is `false`, or when `MUT_DECLINED=1` **unless** `REOFFER_MUT=true`, in which case ask once more - the prior decline predates a major bump). Frame the code modification explicitly - not "run a deeper test analysis?" but "**this will modify your source files** (mutating up to 5 focus files) and run your test suite over the changes, time-boxed, then revert". When re-offering, say so: _"You previously declined mutation testing under an older version; the pass has since changed - run it now?"_. Three options, same shape as Steps 2a/2b:

- **Run mutation analysis** - run the bounded pass on the focus files (installing `$MUT_TOOL` first if `MUT_PRESENT=0`, exactly as Step 2b installs a dead-code tool: run the platform-appropriate install, surface any failure as a chat message, and fall back to no-mutation on failure - never block).
- **Skip for now** - continue with the cheap read intact. Don't write a marker; ask again next run.
- **Skip permanently for this repo** - `write_decline_marker "$MUT_TOOL"` so future runs don't ask. Re-declining restamps the marker at the current version, so the re-offer won't repeat within this major.

**On accept (tool available):** run the opt-in mutation pass, then regenerate the heatmap with the survivor overlay. The core re-run reads the `test_focus` targets with test evidence itself, runs `scan_test_pressure(..., opt_in=True)` scoped to them, and rewrites the `test_pressure` block in `run-context.json` in place, together with what derives from it: `mutation_not_run_cap`, the `level` tally with the `headline`, the `log.md` readiness line and `badge.json` worded from it, the `untrusted_hotspot` finding in `derived_findings`, `attention`, `findings_markdown`, `keyhole_summary` and `prescribed_actions`. Read those blocks after this step, not before:

<!-- chat-skip:end -->
```bash
# 1. Re-run the test-pressure scan with the bounded mutation pass enabled
<!-- chat-replace:uv-core-mutation -->
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_core.py" "$REPO_ROOT" --opt-in-mutation  # plus the run's --profile NAME or --levels FILE and every --pack FILE, if it had them

# 2. Regenerate the heatmap with the survivor overlay so covered-but-unpinned
#    files get hatched and stop reading as safe green
<!-- chat-replace:uv-treemap-overlay -->
uv run "${CLAUDE_SKILL_DIR}/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json" --test-pressure "$REPO_ROOT/.assess/run-context.json"
```

The pass runs once per package root among the focus files (the nearest directory with a `pyproject.toml`, `setup.cfg` or `setup.py`), from that directory, under the package's own mutmut config when it has one and a generated one otherwise. mutmut runs the package's suite, so it runs where the suite's dependencies import: a `.venv` / `venv` in the package that already holds mutmut 3 (`runner: venv`, read only: nothing installed into it, no bytecode written), else, for a `pyproject.toml` with a `[project]` table and `uv` on PATH, a scratch environment `uv run --project` builds in the temp copy from the package's dependencies and default dependency groups (not `[project.optional-dependencies]` extras, so test dependencies kept in an extra still fail the baseline run, and the reason says which import) plus `mutmut==3.8.0` and pytest (`runner: uv`; first use may download them, and the time counts against the pass's budget), else the `mutmut` on PATH (`runner: path`), which serves only a suite with no third-party test dependencies. `test_pressure.mutation_groups` lists each run with its `root`, `config` (`repo` or `generated`), `runner` (absent on a group that stopped before a runner was chosen: budget, config gap or copy error), its `reason` when it could not run or stopped early (a failed baseline run names the first import error or failing test), `partial: true` when it hit the time box and kept only the mutants it had tested, and any `unmeasured` focus files (outside the package config's scope, or not reached before a partial stop) when it ran without them. The pass counts as run when any group produced mutant records; in the report, name each group that did not run or is `partial` and quote its `reason`, and name the `unmeasured` files, so a partial pass reads as partial. A `partial` group's figures are the fastest mutants only, a biased sample, so when every group with records is `partial` the Layer 6 cap holds at Partial (`mutation_not_run_cap.partial`, Step 3).

<!-- chat-skip:start -->
With no mutation data the `--test-pressure` flag is a silent no-op, so the overlay regeneration is harmless even if the pass produced nothing.

**On decline or no tool available:** continue with the cheap read intact - the assessment is complete without it. State in the report that the deep mutation pass was **not** run and why (declined, or no mutation tool for the language), so the Layer 1 read is honest about its depth rather than implying the focus files were proven well-tested.
<!-- chat-skip:end -->

## Step 3: Score the Layers

The deterministic core has written the data bus (`.assess/run-context.json`). Assigning each layer Present / Partial / Missing is judgement-heavy work that benefits from a fresh context window applying the layer methodology - so it runs as a dedicated unit, not inline here.

**Layer 6 (truth pressure) is capped at Partial when mutation testing did not run.** Read `mutation_not_run_cap` from `run-context.json`: when `applies` is true (the default read-only pass leaves it true - mutation only runs on the opt-in Step 2d accept), Layer 6 **cannot** be scored Present. A Present verdict there claims the suite *proves* behaviour, which only a mutation run substantiates - absent it, the strongest honest verdict is Partial, annotated with `mutation_not_run_cap.annotation` (`truth pressure not yet measured (mutation not run)`). This is enforced deterministically: `assess_finalize.py` refuses a finalize-input whose Layer 6 score exceeds Partial while `mutation_run` is false, so scoring it Present will fail the finalize step, not merely read wrong. The same hold applies when mutation ran on partial evidence only: `mutation_not_run_cap.partial` is true when every mutation group that produced records stopped at the time budget, `applies` stays false (mutation did run), `max_layer6_band` is Partial, and the annotation is `truth-pressure partial (mutation stopped at budget)`; finalize refuses Present here too. One complete group lifts the hold, since its files carry full mutation evidence.

<!-- chat-replace:layer-scorer-delegate -->
Spawn the `assess-layer-scorer` agent (`subagent_type: "assess-layer-scorer"`), passing `REPO_ROOT`. It reads `.assess/run-context.json`, gives every practice a verdict, and returns the per-practice verdicts with evidence and the structured `evidence` list Step 4 re-checks. Hold that scorecard for Step 4.

## Step 3.5: Read Cross-Run Context

Before the report is written, check what changed since the last run (the findings-writer renders this into the report's diff section):

```bash
jq '.diff, .diff_detail' "$REPO_ROOT/.assess/run-context.json"
```

If `prior` was None (first run), skip this section in the report.

**Check `diff_reliable` first.** The reliability check is schema- and version-aware, not a blunt exact-version match: a MINOR/PATCH plugin bump keeps `diff_reliable: true` and the trend armed, but the diff is voided (`diff_reliable: false`, with `diff_version_note` naming the cause) when the stats `schema_version` changed, a complexity backend moved (`lizard`/`scc` version delta - the note names the tool), the prior snapshot never stamped a version, or the plugin MAJOR version changed. A MAJOR bump also sets `diff_trend_reset: true` - the report renders an explicit "Trend baseline reset" disclosure so a suppressed diff isn't misread as an unchanged run. **Suppress the "What Changed Since Last Run" section** whenever `diff_reliable` is false and surface the `diff_version_note` (as the deterministic renderer already does) rather than the transition lists. Otherwise, populate the section:

- **Graduated** (good): list paths from `diff_detail.graduated` - hotspots that left the top list
- **Regressed** (bad): list paths from `diff_detail.regressed` with their `max_fn_ccn_delta` / `ccn_delta` / `commits_delta`. A file regresses when its worst function's ccn rose, when its summed ccn rose by more than its worst function fell, or - with the worst function flat or unmeasured (`max_fn_ccn_delta: null`, e.g. scc-scored files) - when its summed ccn rose or it grew >50 LOC across >2 commits
- **Restructured** (good): list paths from `diff_detail.restructured` - summed ccn rose but the worst function fell by at least as much, the trace of splitting a function into named helpers. Never call these regressions: the sum rises because each extracted helper adds its own +1
- **New** (watch): list paths from `diff_detail.new`
- **Persistent** (structural debt if N runs in a row): list paths from `diff_detail.persistent`

The wiki files at `.assess/index.md` and `.assess/hotspots/*.md` are already updated by `assess_core.py` - you don't need to write them. You only write the prose summary in `assess-report.md`.

## Step 4: Write the Report

<!-- chat-replace:evidence-check -->
**Verify the scorecard's evidence first** - the only point where a false claim can still be kept out of the report. Write the scorer's `evidence` list to `$REPO_ROOT/.assess/.cache/evidence.json` (after `mkdir -p "$REPO_ROOT/.assess/.cache"`), then run the check: `uv run "${CLAUDE_SKILL_DIR}/scripts/lib/report/evidence_check.py" "$REPO_ROOT" "$REPO_ROOT/.assess/.cache/evidence.json" --json "$REPO_ROOT/.assess/.cache/evidence-checked.json"`. It prints `verified N, rejected M` and exits 0 (all hold) or 1 (some rejected); without that line, or with no `evidence-checked.json`, the check did not run - fix it before writing, never read it as a pass. The output's `evidence` replaces the scorer's list in the scorecard handed on; entries under `evidence_rejected` (each with a `reason`) are handed on beside it as the record of refuted claims, never cited as fact. When every entry a layer cited was rejected, re-score that layer yourself from `run-context.json` and its remaining verified entries, not from the scorer's prose. Delete both files once read. Assembling `.assess/assess-report.md` - the scorecard, the snapshots, the verbatim cross-practice findings, the findings where the repository says one thing and does another, and the mandatory Top 3 Actions - is a reusable, mostly-deterministic procedure. It runs as a sub-skill.

<!-- chat-replace:findings-delegate -->
Use the assess-findings skill, handing it the scorecard the layer-scorer returned. It assembles `.assess/assess-report.md` from the data bus plus the scorecard: the verbatim findings section, the findings where the repository says one thing and does another, and the Top 3 Actions (the attention list is mandatory). Then continue to Step 7.5.

## Step 7.5: Finalize the wiki (required)

After writing `assess-report.md`, write `finalize-input.json` to the transient cache and invoke `assess_finalize.py` so the wiki files reflect the actions you chose.

The input file lives under `.assess/.cache/` rather than directly in `.assess/` because it is a one-off LLM-authored input consumed immediately - it has no future utility and only creates noisy diffs if committed. `assess_finalize.py` reads the cache path first (and falls back to the legacy in-tree location if a prior run wrote one there), then **deletes** it on success so it cannot leak into a commit either way.

````bash
mkdir -p "$REPO_ROOT/.assess/.cache"
cat > "$REPO_ROOT/.assess/.cache/finalize-input.json" <<'EOF'
{
  "run_id": "<copy run_id verbatim from run-context.json>",
  "layer_scores": {"0": 1.0, "1": 0.5, "2": 1.0, "3": 0.5, "4": 1.0, "5": 0.5, "6": 0.5, "7": 1.0, "8": 0.5}, "evidence": [{"layer": 0, "kind": "path_exists", "path": "CLAUDE.md"}],
  "top_action": "Add cyclop rule (threshold 15) to .golangci.yml",
  "hotspot_actions": {
    "src/foo.go": [
      "Split parseLine into smaller functions",
      "Add a test file at src/foo_test.go"
    ]
  },
  "actions": [
    {
      "rank": 1,
      "action": "Add cyclop rule (threshold 15) to .golangci.yml",
      "layer": 3,
      "condition": "layer-3-linters.2",
      "effort": "small",
      "files": [".golangci.yml"],
      "first_step": "Add 'cyclop' with max-complexity: 15 under linters",
      "done_when": "`golangci-lint run` passes with the rule active; no new suppressions added",
      "scope_fence": "Only .golangci.yml; do not edit source files to chase pre-existing violations"
    }
  ]
}
EOF

<!-- chat-replace:uv-finalize -->
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_finalize.py" "$REPO_ROOT"
````

This replaces:
- The `**Top action:** (LLM fills in)` placeholder in this run's `log.md` entry (found by its `assess:run_id` stamp) with your Top 1 action; the log chain is re-computed. The entry's `**Readiness:**` line is the headline summary the core already wrote (`headline.summary`); you never set it.
- Each `hotspots/<slug>.md`'s `Suggested actions` section with the actions you derived for that file, and the top card's Start here and Done when (in `run-context.json` and `assess-report.md`) with the first open action of the `actions.json` it has just written, so the card names this run's Top 3 rank 1 rather than the previous run's.

On a scoped run, add `--scope "$SCOPE"` so finalize uses `.assess/<slug>/`. **The minimal input** is `run_id` and `top_action`; every other field is optional. If finalize exits 1 because an earlier same-date entry is still unfilled (a run on an older commit that was never finalized), run the `--drop-entry <run_id>` command its message prints (it leaves a one-line tombstone), then re-run finalize. Never delete a log entry by hand: it breaks the chain.

**`assess_finalize.py` reconciles this input against `run-context.json` before writing anything, and refuses (writing nothing, exiting non-zero) on any violation.** So the fields must be internally honest:
- `run_id` - **copy it verbatim** from `run-context.json`. It proves the input was authored against *this* run; a mismatch is treated as a torn write and rejected.
- An action's optional `condition` is the maturity-model condition it moves (`<row id>.<level>`, as `level.conditions[].id` in `run-context.json` lists it, e.g. `layer-5-ci.1`). An id the run's level result does not list is rejected, naming it; `actions.json` carries the id through.
- Every key in `hotspot_actions` must be a real top hotspot from `stats_summary.top_hotspots` - a fabricated path is rejected, naming the path.
- `layer_scores` maps each layer id to its band (Missing 0.0 / Partial 0.5 / Present 1.0). Layer 6 must not exceed **0.5** when `mutation_not_run_cap.applies` or `mutation_not_run_cap.partial` is true (see Step 3). Include it so the cap is enforced; a legacy input omitting it skips only the Layer 6 check. Set `evidence` to the verified list the pre-report `evidence_check` run kept (flat `{layer, kind, path[, needle]}` entries), never the scorer's raw list; finalize re-checks it against the repository: a layer whose entries are all rejected is refused, naming each entry by kind, path and needle; a layer with at least one verified entry finalises, and each rejected entry prints a `finalize: warning:` line on stderr, which you relay to the user. An input without `evidence` skips the check.

The live README badge (`.assess/badge.json`, shields.io endpoint schema) is deterministic: `assess_core.py` writes the headline summary on every run (`headline.summary`: the Step, the practices fully in place and its next blocking gate, e.g. `Step 3 · 2 practices fully in place · next: coverage gate`) and links it to `assess-report.md`. Finalize never rewrites it, so the badge only ever claims what a deterministic run can reproduce. When offering the PR (assess-pr), include the embed snippet if the repo's README has no badge yet:

```markdown
![AI-readiness](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2F<owner>%2F<repo>%2F<default-branch>%2F.assess%2Fbadge.json)
```

The `actions` array mirrors the report's Top 3 Actions table one-to-one and **must carry every table row** - `rank`, `action`, `done_when`, and `scope_fence` are required per entry (`layer`, `condition`, `effort`, `files`, `first_step`, `finding` recommended). Set `finding` to the finding type the action addresses (e.g. `hidden_coupling`, `lying_map`, `unexplained_complexity`, `untrusted_hotspot`, `self_referential_tests`, `refactor_boundary`) so `assess_finalize.py` can stamp the deterministic execution `mode` an executor should take; an action with no `finding` defaults to the conservative `characterize_first` mode. `assess_finalize.py` writes it to `.assess/actions.json`, the *durable* machine-readable contract (schema 2): unlike this input file (consumed and deleted), `actions.json` persists so an executing agent - including a smaller, cheaper model - can pick up the work with its exit criteria, fences, execution mode, and lifecycle status intact, without parsing the report's markdown. Re-running `/assess` preserves each action's `status`/`claimed_by`/`completed_sha`, matching on `finding` plus `files` and falling back to the directive text, so a completed action stays done even when you reword it; a done action that leaves the Top 3 moves to a `completed` list. For a `self_referential_tests` action, put the prescribed hotspot `path` (the source file) in `files`, alongside any test file. A committed `done` entry for `self_referential_tests` naming the source path in `files` clears that path from the Top 3 while the source is unchanged since `completed_sha` (it stays in `derived_findings` with a `reviewed` note). See [`references/actions-schema.md`](references/actions-schema.md) for the full schema.

Without this step, the `log.md` placeholders above carry forward forever. Hotspot pages you don't supply actions for keep a neutral pointer (`This file is flagged but outside this run's Top 3. See the report's Top 3 Actions, or run a focused /assess pass for file-specific guidance.`) rather than an unfinished-work placeholder - a flagged-but-not-Top-3 page reads as intentional.

The hotspot_actions dict should include at minimum the files mentioned in your Top 3 Actions. You can include more if you have specific suggestions for them; any file you omit keeps the neutral pointer.

## Step 8: End-of-Run Offers

With the report written and the wiki finalized, run the end-of-run offers - open a PR, track the Top 3 Actions, freeze the assessment into a CI gate, the tool-feedback prompt, and the **uninstall** escape hatch (remove everything this run wrote, per `run-context.json .uninstall_instructions_path`). These are batched into Phase 2's single question and honour the non-interactive contract. This is a reusable procedure (akin to `pr-review-merge`), so it runs as a sub-skill.

<!-- chat-replace:pr-delegate -->
Use the assess-pr skill. It runs the write-back offers (PR, issue tracking, freeze-into-CI) plus the tool-feedback prompt and the uninstall offer, reading the written `.assess/assess-report.md` artifact - notably mutating the Top 3 Actions table's `Issue` column in place when the user creates tracking items.
