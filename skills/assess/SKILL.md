---
name: assess
description: "Assess a codebase's readiness for AI agent contributors using the layered contract model, and generate a complexity hotspot SVG treemap (size = LOC, hue = cyclomatic complexity, saturation = recent git churn). TRIGGER when the user types /assess, asks for an AI-readiness review, wants a complexity heatmap or hotspot map, asks 'how complex is this code?', wants migration risk triage, or asks for a codebase snapshot/report. Produces an MD report + SVG that can be opened as a PR in the target repo."
---

# AI Readiness Assessment + Complexity Hotspot

Three artefacts in one pass against a target repo:

1. **Layered contract assessment** — 0–8 score across navigability, runtime liveness, code design, linters, architecture tests, CI, coverage, review bots, and AI project management.
2. **Complexity hotspot SVG** — Codecov-style treemap of the code. Size = LOC. Colour = cyclomatic complexity. Saturation = recent git churn. Vivid red = complex AND active = riskiest to change.
3. **Doc navigability SVG** — a node-graph of the docs. Structure = connectivity (centre = entry, rim = unreachable, dashed ring = orphan); colour = staleness (vivid red = a frozen doc beside churning code = a *lying map*); size = file length. Folds navigability and the decaying-map signal into one artifact.

Both SVGs are colour-blind-safe by default (OrRd ramp, no red-green).

All land as files inside the target repo. The skill always writes them locally; after writing, **ask the user** whether to open a PR in the target repo with the artefacts.

## The model: truth-pressure, not presence

Read this before scoring — it changes how you score. Across every layer, the real signal is never **presence**. It is whether a thing is under **active pressure to stay true**:

- Tests keep **behaviour** honest (CI fails when it's wrong).
- Retros / feedback loops keep the **process** honest (Layer 8 scores whether retros are *carried out*, not merely present).
- Maintenance keeps **docs** honest (a wiki tracked against code churn).
- Telemetry / liveness keeps **relevance** honest (is this code actually exercised).

So **AI-readiness is the degree to which a codebase's self-descriptions are kept honest, not the degree to which scaffolding exists.** Score artefacts on *maintenance pressure*, not existence. A stale-but-present doc scores **at or below absent**: missing makes the agent go look; confidently-stale makes it navigate fast to a wrong, current-looking conclusion.

The 9 layers (0–8) fall into three bands, ordered by dependency — what must hold for the next band to mean anything:

- **Read-side foundation** (L0 navigability, L1 liveness) — can the agent form a *true picture* before it acts?
- **Write-side enforcement** (L2–L7) — can the agent be trusted to produce good output? Only means something once you can trust that what you're reading is real and current.
- **Meta** (L8 feedback) — does the system keep itself honest over time? Depends on a working enforced system to improve, so it stays last.

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
- `$REPO_ROOT/.assess/doc-graph.svg`
- `$REPO_ROOT/.assess/assess-report.md`

## Step 2: Generate the Code Heatmap + Doc Graph

This step produces **two** views of the codebase, both colour-blind-safe (OrRd ramp, no red-green):

- **Complexity heatmap** (`complexity-heatmap.svg`) — a treemap of the *code*. Size = LOC, colour = cyclomatic complexity, saturation = recent churn. Vivid red = complex AND active = "hard to change safely".
- **Doc navigability graph** (`doc-graph.svg`) — a node-graph of the *docs*. Structure shows connectivity (centre = entry point, rings = link-distance, rim = unreachable; orphans carry a dashed ring); colour shows staleness in the same grammar as the code heatmap (vivid red = a frozen doc beside churning code = a lying map); size = file length. It folds both Layer 0 doc signals — navigability and the decaying-map — into one artifact.

Feed the complexity stats into the linter/complexity layer (Layer 3) and the `doc_graph` / `doc_staleness` blocks of `run-context.json` into **Layer 0** (the graph SVG is the visual; the score reads the structured blocks).

### 2a: Offer to install `scc` (one-time per repo)

The bundled treemap uses [`lizard`](https://github.com/terryyin/lizard) (Python, Go, JS, Java, C/C++, etc.) by default. Optional `scc` extends coverage to 200+ languages including markdown, JSON, YAML, SQL, and shell — useful when the repo's surface is more than just traditional source code.

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

**Offer the install only if all three are true:** `SCC_PRESENT=0`, `SCC_DECLINED=0`, and the repo looks lizard-sparse (`CODE_FILES < NONCODE_FILES` or `CODE_FILES < 10`). Otherwise skip straight to 2b (or 2c if 2b has nothing to offer).

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

### 2b: Offer to install per-language dead-code tools (one-time per tool)

Layer 1's intra-repo dead-code scan calls a per-language tool (`vulture` for Python, `ts-prune`/`knip` for TS/JS, `staticcheck`/`deadcode` for Go). When the tool is absent, the scan degrades to `tool_absent` and the user has no resolution path inside the skill - they'd have to know which tool fits the language, which package manager to use, and run the install themselves. The same install-offer pattern as Step 2a closes the loop without leaving them to figure it out.

Detect languages with cheap `fd` counts (mirroring Step 2a's heuristic - the treemap script's own classification isn't exposed in the stats sidecar, and shelling out is fine here):

```bash
PY_FILES=$(fd -t f -e py . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
TS_FILES=$(fd -t f -e ts -e tsx . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')
GO_FILES=$(fd -t f -e go . "$REPO_ROOT" 2>/dev/null | wc -l | tr -d ' ')

# Per-language candidate tool. Prefer the read-only tool first - `ts-prune` over
# `knip` for TS, `staticcheck` over `deadcode` for Go - so the user isn't asked
# twice for the same job and the chosen tool doesn't need to build the project.
needs_offer() {
  # $1 = tool; $2 = file count for the language; returns 0 if we should ask.
  local tool="$1" count="$2" min="${3:-5}"
  [ "$count" -ge "$min" ] || return 1
  command -v "$tool" >/dev/null 2>&1 && return 1     # already installed
  [ -f "$REPO_ROOT/.assess/.no-$tool" ] && return 1  # user declined permanently
  return 0
}

OFFERS=()  # each entry: "language|tool|install_cmd"
needs_offer vulture "$PY_FILES"      && OFFERS+=("python|vulture|pip install vulture (or 'uv tool install vulture')")
needs_offer ts-prune "$TS_FILES"     && OFFERS+=("typescript|ts-prune|npm install -g ts-prune")
needs_offer staticcheck "$GO_FILES"  && OFFERS+=("go|staticcheck|go install honnef.co/go/tools/cmd/staticcheck@latest (or 'brew install staticcheck')")
```

If `OFFERS` is empty (no language hits the threshold, or every tool is already installed/declined), skip straight to 2c.

Otherwise, batch the questions into **a single AskUserQuestion call** — one question per language in `OFFERS`, three options per question:

- **Install <tool>** — run the cited install command and continue.
- **Skip for now** — proceed without the tool. Don't write a marker; ask again next run.
- **Skip permanently for this repo** — write `$REPO_ROOT/.assess/.no-<tool>` so future runs don't ask. Recommended when the language only appears in scripts/configs that don't warrant symbol-level reachability.

Phrase each question so the gain is concrete, e.g.:

> "This repo has 47 Go files. `staticcheck -checks U1000` would let `/assess` flag unreachable Go funcs as Layer 1 candidates. Install? (`go install honnef.co/go/tools/cmd/staticcheck@latest` or `brew install staticcheck`)"

When the user picks **Install <tool>**, run the platform-appropriate command from the offer. Surface any install failure as a chat message and continue - dead-code tools are degrade-don't-block (same contract as scc); a missing tool reduces Layer 1's precision but never gates the assessment.

When the user picks **Skip permanently for this repo**, write the marker:

```bash
mkdir -p "$REPO_ROOT/.assess"
touch "$REPO_ROOT/.assess/.no-<tool>"   # e.g. .no-staticcheck
```

For multi-language repos with several offers, the AskUserQuestion call lists every language in one prompt rather than serialising. The user answers once and the run proceeds with whichever tools they accepted.

### 2c: Run the treemap

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

<!-- chat-skip:start -->
# Resolve this skill's own directory so we can run its bundled scripts. A
# plugin install exposes $CLAUDE_PLUGIN_ROOT (the plugin root in the version
# cache, e.g. ~/.claude/plugins/cache/<mp>/<plugin>/<ver>/); fall back to a
# hand-placed ~/.claude/skills/assess/ copy when it isn't set. CLAUDE_PLUGIN_ROOT
# is an environment variable, so it stays valid across later steps' shells too.
SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
<!-- chat-skip:end -->

# Run the complexity treemap (produces fresh complexity-stats.json)
# (single line: the standalone transform replaces the marker + one following line)
<!-- chat-replace:uv-treemap -->
uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json"

# Run the doc navigability graph (connectivity + staleness in one SVG; feeds Layer 0)
<!-- chat-replace:uv-doc-graph -->
uv run "$SKILL_DIR/scripts/doc-graph-svg.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/doc-graph.svg"

# Run the deterministic core (instruction grading, doc link-graph, doc staleness,
# liveness/dead-code, observability rungs, stats diff, wiki files, run-context.json)
<!-- chat-replace:uv-core -->
uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT"
```

Either SVG is additive: if a script fails (no `uv`, no scoreable files, no docs), record "could not be generated — <reason>" in the report and continue. The doc graph shares its data with the deterministic core's `doc_graph` / `doc_staleness` blocks, so even when the SVG can't render, Layer 0 still scores from `run-context.json`.

Now `$REPO_ROOT/.assess/run-context.json` contains the structured data you need for the prose sections. Read it before writing the report.

The `plugin_version` field in `run-context.json` tells you which plugin version produced this run. Surface it at the top of the report (e.g., "Generated by `/assess` v1.8.0") so readers can spot it if a stale cached version of the plugin produced unexpected output.

## Step 3: Scan Each Layer

Run these checks in parallel where possible. For each layer, collect evidence and assess quality.

### Layer 0: Agent Instructions & Navigability (Read-Side Foundation)

Layer 0 answers: *can the agent form a true picture before it acts?* It has two halves — the agent instruction files (static intent) and the **navigability of the docs** (can the agent traverse to the right place, and is what it finds still true). Score both on maintenance pressure, not presence.

#### 0a — Agent instruction files

Read the agent instruction file grades and surface integrity from `run-context.json`:

```bash
jq '.instruction_files, .instructions_grade, .untracked_instruction_files, .broken_instruction_refs' "$REPO_ROOT/.assess/run-context.json"
```

`.instruction_files` is a dict keyed by filename (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md`). Only **git-tracked** files are graded — a file present on disk but uncommitted is *not* credited (it isn't part of what the repo ships). The same heuristic grader scores each tracked file. For each:
- `grade: "A" | "B+" | ...` - report verbatim per file
- `subscores.positive_directives` - count of positive directives found
- `subscores.tradeoff_phrases` - count of reasoning phrases
- `subscores.path_references` - count of file path references
- `freshness_days` - days since last edit

`.instructions_grade` is the best grade across all tracked files - the starting point for the layer scoring.

**Scoring rule:** start from the deterministic grade, then apply the integrity overrides below.
- `instructions_grade` is `null` → **Missing** (no committed instruction file at any known location)
- A/A-/B+ → **Present**
- B/C → **Partial**
- D/F → **Partial** if at least one file exists but scores low - note the grade and recommend rewriting

**Integrity overrides (these can pull the half *below* the grade — a single good file does not rescue a broken instruction surface):**
- **`broken_instruction_refs` is non-empty → cap the instruction half at Partial, or Missing if it's the *primary* instruction surface that's broken.** These are advertised-but-broken instruction references: a committed `.cursorrules`/`AGENTS.md`/`CLAUDE.md` that is a **dangling symlink** (`reason: symlink target missing`), or an entry doc that **links to a missing instruction file** (`reason: link to missing instruction file`). The truth-pressure model says a broken map scores at or below absent — a repo that *advertises* `.cursorrules` but the symlink dangles is worse than one that never claimed to have it. Name each broken ref and make "fix or remove the broken instruction reference" a Top-3 action. Do **not** let an unrelated file that happens to grade B+ keep the half at Present. **But don't let the score mislead either:** if a committed instruction file still grades passing while the *advertised* surface dangles (e.g. a B+ `.github/copilot-instructions.md` while a referenced `CLAUDE.md` is missing), prefer **Partial** over Missing, and in the Evidence cell name that committed file and its grade. A human reviewer would call this Partial; reserve **Missing** for genuine absence. Either way the report must read as *the advertised surface is broken*, never *no instructions exist*.
- **`untracked_instruction_files` is non-empty → note it, don't score it.** An instruction file exists on disk but isn't committed, so nobody who clones gets it. Report "`<file>` present locally but untracked — commit it or it won't reach contributors (human or agent)"; never let it raise the grade.
- **Surface within-band regression.** The Present/Partial/Missing bucket is coarse — "lost the primary instruction file and gained 40 dangling links" may not move the bucket. When `broken_instruction_refs` or a large `doc_graph.dangling_links` count appears, call out the regression explicitly in the report even if the band label is unchanged.

Important: a null grade and an F grade map to different remediation. Null means "create a CLAUDE.md / AGENTS.md (whichever the team uses)." F means "the file is there but needs rewriting." A broken/dangling reference means "the file you point at isn't reachable — fix the link or remove the claim." Don't conflate them.

For a dangling reference, the right verb depends on the target's actual state — **don't default to `git add`**, since the file may never have been written. Check `untracked_instruction_files`: if the target appears there it exists on disk, so the action is *commit it* (`git add <file>`); if it appears nowhere, it was never created, so the action is *write it* (then commit) — or *remove the dangling claim*. "`git add CLAUDE.md`" is only correct when `CLAUDE.md` is actually present-but-untracked.

When multiple instruction files are present (e.g. CLAUDE.md and AGENTS.md as symlinks of the same content), list each in the report.

This replaces the prior subjective "is it generic?" check. The grader rewards positive directives and tradeoff reasoning; it penalizes pure-negative framing and staleness.

#### 0b — Navigability (measured as a graph, not a presence check)

Navigability is a graph property, so the deterministic core measures it as one. Read the doc link-graph, staleness, and association signals from `run-context.json` — **do not re-scan for files by name**; the graph already identifies hubs by centrality without filename guessing:

```bash
jq '.doc_graph, .doc_staleness.association, .doc_staleness.modularity, .stale_hubs[:5]' "$REPO_ROOT/.assess/run-context.json"
```

Recognise the full range of navigability artefacts (not just README/ADR/API specs): a Map-of-Content (MOC) / index note, a linked-doc graph (cross-referenced markdown — the [Karpathy-pattern LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)), `AGENTS.md`, and **repo skills** (`.claude/skills/`, `skills/`). The graph already accounts for all of these. The signals below are the deterministic subset of Karpathy's wiki "Lint" health-check — hubs, orphans, connectivity, dangling references — applied to a code repo's docs.

Score navigability from these signals:

- **Connectivity / reachability (a wayfinding signal — read it as *curation*, not *access*).** A *good* doc set is one connected island, fully reachable from the entry points (README / `AGENTS.md` / top MOC). `doc_graph.island_count == 1` and `reachability_pct` near 1.0 is navigable; a high `orphan_rate` or many islands weakens wayfinding for both humans and agents. **Keep the claim honest:** link-reachability measures whether the docs are *curated into a navigable map*, not whether content is *reachable at all* — an agent can always `ls docs/` and open any file by path. So low reachability means **uncurated** (poor signal-vs-noise / weak wayfinding), not **inaccessible**. Weight it accordingly — an index/MOC is worth adding, but a directory-organised docs tree with low link-reachability is not the emergency "an agent can only discover 9%" makes it sound. Name the orphan docs.
- **Hubs / MOCs by centrality.** `doc_graph.hubs` are the load-bearing docs (highest PageRank). A stale hub is the most dangerous lying map — everything routes through it.
- **MOC validation (declared vs structural).** `doc_graph.moc_named_but_not_wired` lists docs *named* like a map (`index.md`, "MOC") that aren't structural hubs — named but not wired. Flag each as a finding: the graph shows the map isn't actually built.
- **Broken links / ghost files.** `doc_graph.broken_links` lists links whose target file doesn't exist — `{from, target, kind}`. The doc graph draws each as a labelled "ghost" node, and the missing name *is* the suggested fix (create the file or correct the link). Name a few and recommend the fix; `dangling_links` is the count. `ambiguous_wikilinks` (a bare `[[name]]` matching several files) is a milder smell worth noting.
- **Missing cross-references.** `doc_graph.missing_xrefs` lists `{from, to}` pairs where a doc *names another doc's filename in prose but never links to it* — "concepts mentioned but lacking the connection". Suggest adding the links so traversal (human or agent) actually reaches them.
- **Contradictions are out of deterministic scope — hand them to an LLM.** The remaining lint check — contradictions between pages — can't be detected structurally. When the doc set is non-trivial, add a Top-3 / Additional action telling the user to have *their* agent read the doc set for contradictions and stale claims (e.g. "run your LLM over `docs/` to flag pages that contradict each other or the code"). State plainly that `/assess` does not check this.
- **Centrality × staleness (the priority signal).** `stale_hubs` is ranked by `pagerank × staleness ratio`. The top entry — a central doc whose subject code is churning while the doc sits frozen — is a top finding. This feeds both the docs heatmap and this score.
- **Doc→code association as a maturity signal.** `doc_staleness.association.pct_code_under_base_doc` and `pct_docs_mapping_to_code`: if the doc→code map is derivable from structure (clean hierarchy/convention), that's positive navigability; docs floating disconnected from the code they describe is a gap.
- **Modular base docs, size-weighted.** `doc_staleness.modularity`: a module owning a *maintained* base doc is what makes a large codebase navigable to humans and deterministically mappable for an agent. **Weight by size** — do not penalise a small/single-purpose repo (`large_repo: false`); for a `large_repo: true` with low `base_doc_coverage`, flag the Layer 0 gap with remediation "decompose into modules; add a maintained base doc per module." The per-module docs are held to the same maintenance standard — the target is *modular + maintained*, never just "more docs."

**Truth-pressure ordering of navigation aids** (weight maintained/executable aids higher): executable aids that run in CI (skills, doctests) **>** tests **>** linked-doc graph / MOC **>** prose. Executable aids fail loudly when they rot; prose rots silently.

**Scoring rule (mirror the null-vs-F split):** a wiki/MOC/`AGENTS.md` scores **Present** only when *maintained* — its churn tracks the code's churn (low `stale_hubs` ratios, `island_count` ≈ 1, reachability high). Score **Partial/Missing** when stale relative to code churn or fragmented, and flag stale-but-present as **actively misleading** — score it at or below absent. Combine with 0a: strong instruction files **and** a maintained, navigable doc graph → Present; good instructions but a stale or fragmented doc set → Partial; neither → Missing.

### Layer 1: Runtime Legibility / Liveness (Read-Side Foundation)

Layer 1 answers: *is this code actually live, and can the agent find out?* A subsystem can pass every write-side layer — compiles, typed, linted, tested, reviewed — and still be **dead**: the data feeding it stopped, the consumer was re-pointed, but the code remains. Finding the code does not mean it is still used. This layer gates the entire write side: enforcing types/lint/coverage on dead code is wasted at best, misleading at worst.

Read both tiers from `run-context.json`:

```bash
jq '.dead_code, .observability' "$REPO_ROOT/.assess/run-context.json"
```

**Deterministic tier — intra-repo dead code (`dead_code`).** A best-effort scan (`vulture`/`ts-prune`/`knip`/`staticcheck`/`deadcode`) flags unused exports / unreferenced symbols. `dead_code.tools` reports per-language status; `candidate_count` and `candidates` list the findings (already filtered to *this* repo — vendored/build dirs are excluded). Surface them in the report **with the explicit caveat** from `dead_code.caveat`: static reachability proves "nothing in *this* repo calls it," never "no external consumer calls it." Cross-boundary liveness needs the next tier. When `available: false`, report "intra-repo dead-code scan not run (no language tool present)" — degrade, don't penalise.

Two `tools[].status` values need handling in the report: `available_not_run` means the tool is present but would **build the project** (`deadcode`/`staticcheck`/`knip` resolve/compile and may write the module cache or hit the network), so a read-only assessment doesn't run it — surface the tool's `reason` (it includes the exact command) as a "run manually to cross-check" follow-up rather than a finding. `timeout` / `tool_absent` likewise degrade, not penalise.

**Observability tier (the decisive one) — three rungs (`observability.rung`, 0–3):**

1. **Instrumented** — telemetry is emitted (OpenTelemetry, Prometheus, Datadog/APM, structured logging). Necessary, not sufficient.
2. **Discoverable** — an `OBSERVABILITY.md` / runbook tells the agent *where* runtime truth lives. Orients, but grants no access.
3. **Reachable** — the agent has an *invokable* path to runtime state: an MCP server over logs/metrics/traces (`.mcp.json`), a repo skill that tails logs / queries metrics, or a runbook with runnable query commands. **This is the rung that decides the score** — without it the agent *knows* telemetry exists but cannot *use* it, so liveness stays unverifiable in practice. A Grafana no agent can query from stops at rung 1. **Boundary:** this scores what the *repo provides* toward agent-reachability; it cannot know the agent's live environment — say so in the report.

**Scoring (by the rungs):**
- **Present** — reaches **rung 3** (agent can read logs/metrics/traces via an invokable tool/skill/MCP) and dead code is removed or flagged.
- **Partial** — instrumented, maybe discoverable, but **not agent-reachable** (rung 1–2 — the `meridian` case: telemetry exists, the agent can't use it), or candidate-dead code present and unflagged.
- **Missing** — no runtime instrumentation (rung 0); liveness unknowable from the repo.

If `observability.available` is `false` (rung `null`), the scan itself failed — report "liveness not assessed" and degrade, don't score it Missing (that's a real "rung 0", a different finding).

**Encode the liveness asymmetry** in the report: *no traffic is strong evidence of dead; some traffic is weak evidence of live* (could be a healthcheck, a zombie client, or a once-a-year batch).

**Encode the honest limit:** some liveness facts live only in people's heads ("kept for Legal, not wired up") — unreachable by code *or* telemetry. So treat **code-presence as a hypothesis, not a fact**: write "`X` is PRESENT; liveness NOT confirmed; needs telemetry or a named human," never assert liveness.

### Layer 2: Code Design (Compile-Time Correctness)

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

### Layer 3: Linters (Style and Correctness Enforcement)

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
  echo "complexity-stats.json not present; scoring Layer 3 on linter config alone."
fi
```

If the sidecar is missing, skip the combined-scoring matrix below and fall back to the original Layer 3 rule: Present if linter config includes AI-relevant rules (including complexity/length), Partial if linter exists without them, Missing if no linter at all. Record "treemap unavailable" in the Evidence column so the gap is auditable.

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

### Layer 4: Architecture Tests (Conventions as Contracts)

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

### Layer 5: CI Pipeline (Automated Safety Net)

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

### Layer 6: Coverage Gates (Test Completeness Enforcement)

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

### Layer 7: Automated Code Review (Design-Level Feedback)

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

### Layer 8: AI Project Management (Orchestration and Feedback) — Capstone

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

Calculate the score (0-8 based on layers present, +0.5 for partial) and write the report to `$REPO_ROOT/.assess/assess-report.md`.

**Report format** (write this to disk verbatim, filling in the placeholders):

```markdown
# Codebase Assessment: <repo-name>

_Generated <YYYY-MM-DD>._

## How to read this report

This is an improvement roadmap, not a verdict. It measures one thing: **is the codebase kept honest, not just scaffolded.** It pairs three views:

- **Where the codebase is today** — the complexity heatmap shows current complexity and churn. Vivid red = complex AND actively changing = the files most likely to bite an agent (or a human) next week.
- **Whether an agent can navigate it** — the doc graph shows the docs' link structure: how much is reachable from the entry point, and which docs are stale maps of churning code.
- **What keeps it from getting worse** — the AI Readiness score (0–8) across three bands: read-side foundation (can the agent form a true picture?), write-side enforcement (can it be trusted to produce good output?), and meta (does the system keep itself honest over time?).

A codebase can be 8/8 and still on fire (great scaffolding, legacy debt) — or 2/8 with a calm treemap (small codebase, no enforcement needed yet). The views matter together.

**How it's measured.** This is an AI-readiness review run almost entirely on *traditional* tooling — static analysis, git history, and graph metrics over the docs and code. The model only writes the prose around those numbers; it does no scanning itself. That keeps a full run fast and close to zero in model tokens, and makes the structural findings reproducible run-to-run.

The "Top 3 Actions" table at the bottom names specific files. Start there.

## Snapshots

### Complexity — riskiest to change

[![Complexity hotspot](./complexity-heatmap.svg)](./complexity-heatmap.svg)

- **Files scored:** <N>
- **Churn window chosen:** <last 12mo | last 24mo | last 5y | all-time>
- **Complexity profile:** p95 ccn <N> (max <M>); p95 LOC <N> (max <M>)
- **Top hotspots** (composite: complexity × recent churn):
  1. `<path>` — <loc> LOC, ccn <N>, <M> commits in window
  2. ...
  3. ...

Size encodes lines of code, colour encodes cyclomatic complexity (dark red = high), saturation encodes recent git churn (vivid = active). Vivid red blocks are the migration risk.

### Doc navigability — can an agent find its way?

[![Doc map](./doc-graph.svg)](./doc-graph.svg)

Read the structured signal from `run-context.json` (`.doc_graph`, `.doc_staleness`, `.stale_hubs`) and write this section in **plain language** — explain the metrics, don't just dump numbers. Define each term the first time you use it:

- **Navigability, in words.** e.g. _"Of <N> docs, <P%> are reachable by following links from the README; the other <N> are orphans (nothing links to them) or sit in <K> disconnected islands."_ Pull `island_count`, `orphan_rate`, `reachability_pct`, and name 2–3 specific orphans/islands. **Frame it as curation, not access** — do not write "an agent can only discover <P%>": an agent can still `ls docs/` and open any file by path, so low link-reachability means the docs lack a navigable map (weak wayfinding / signal-vs-noise), not that content is hidden. State that adding an index/MOC is the fix, and present it as a wayfinding improvement, not a blocker — for a directory-organised tree, temper the priority accordingly.
- **Attribution.** Describe these checks in plain terms (orphans, broken links, connectivity, hubs). The README credits Andrej Karpathy's LLM-wiki "Lint" pattern as the influence once — **don't repeat that attribution in the generated report.**
- **Lying maps (stale docs of churning code).** Define the terms inline: **stale** = days since the doc itself last changed; **subject churn** = commits in the window to the code the doc describes; **centrality** = how many other docs point to it (a hub). A real lying map is **old AND beside genuinely churning code**. From `stale_hubs` / `doc_staleness.docs`, name the worst 2–3 — but **apply judgment, don't trust the raw composite**: when a doc's `subject_method` is `repo-baseline`, its "subject churn" is just the whole repo's churn (a coarse proxy), so a *recently-changed* doc (low stale-days) that merely happens to be a big hub is **not** a lying map — don't flag it. Prefer docs with a precise association (`nearest-ancestor` / `parallel-docs-tree` / `explicit-links`) and high stale-days.

Colour = staleness (vivid red = a frozen doc beside churning code = a lying map); structure = reachability (centre = entry, rim = unreachable, dashed ring = orphan); size = file length. Hover a node in the SVG (opened on its own) for its path and stats.

## AI Readiness

**Score: X / 8** — <maturity-label>

| Layer | Band | Status | Evidence | Gap |
|-------|------|--------|----------|-----|
| 0: Agent Instructions & Navigability | read | Present/Partial/Missing | <what was found> | <what's missing> |
| 1: Runtime Legibility / Liveness | read | Present/Partial/Missing | <what was found - if rung 3, append: "Reachable *if* the agent has `<tools cited in runbooks>` in its execution environment"> | <what's missing> |
| 2: Code Design | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 3: Linters | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 4: Architecture Tests | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 5: CI Pipeline | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 6: Coverage Gates | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 7: Code Review Bots | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 8: AI Project Mgmt (capstone) | meta | Present/Partial/Missing | <what was found> | <what's missing> |

**Layer 1 evidence carries an explicit caveat by default.** The Layer 1 spec is honest about its boundary: this scores what the *repo* makes agent-reachable, not what the agent has installed at runtime. So when Layer 1 is **Present** (rung 3), the Evidence cell should not read "Reachable, full stop" - it should name the cited tools and conditionalise on their availability. Example: _"Runbook fences `kubectl logs`, `stern`, `logcli`; reachable *if* the agent has these in its execution context."_ When Partial or Missing, the caveat is moot - no rung-3 claim is being made.

### Score derivation (worked)

The layered model has 9 layers (0-8); the display caps at "X / 8" so the headline matches the maturity band table. Compute the raw sum, then apply two rules:

1. **Raw sum**: Present = 1, Partial = 0.5, Missing = 0. With 9 layers, the maximum raw sum is 9.0.
2. **Cap at 8**: the displayed score is `min(raw_sum, 8.0)`. A 9.0 (every layer Present) caps at 8.0 = ceiling. This keeps the band table (0-2 / 3-4 / 5-6 / 7-8) consistent with the headline.

Worked example: 7 layers Present + 2 layers Partial = 7×1 + 2×0.5 = 8.0 raw. Two partials below ceiling shouldn't display as "all perfect", so by the cap rule the **displayed score is 7.0 / 8** - you subtract the half-points the partials cost relative to a Present (each Partial costs 0.5 vs ceiling, two of them cost 1.0, so 8 ceiling - 1.0 = 7.0). Equivalent shortcut: `min(raw_sum, 8 - 0.5 × num_partials_when_raw_sum_would_exceed_8)`. The general case is just `min(raw_sum, 8)`.

Other examples:
- 8 Present + 1 Missing → raw 8.0 → display **8.0 / 8** (AI-Native).
- 6 Present + 2 Partial + 1 Missing → raw 7.0 → display **7.0 / 8** (AI-Native).
- 4 Present + 3 Partial + 2 Missing → raw 5.5 → display **5.5 / 8** (Solid).
- 0 Present + 0 Partial → raw 0.0 → display **0.0 / 8** (Not Ready).

### Maturity Level

| Score | Level | Description |
|-------|-------|-------------|
| 0-2 | Not Ready | Agent will produce inconsistent, unvalidated code |
| 3-4 | Basic | Norms exist but aren't enforced. Agent works but drifts |
| 5-6 | Solid | Contracts catch most issues. Agent is productive |
| 7-8 | AI-Native | System self-improves. Agents work reliably at scale |

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

**Read-side remediation must match the actual gap.** The diagnosis from Layers 0–1 changes the action:

- **Layer 1 Partial because observability is instrumented but not agent-reachable** (rung 1–2 — the `meridian` case): the action is _"make existing observability agent-queryable"_ — e.g. "add a `.mcp.json` log/metrics server" or "add a `view-logs` repo skill wrapping `logcli`" — **not** "add observability" (it's already there; the gap is reachability).
- **Layer 0 Partial because a hub doc is stale:** name the specific stale hub from `stale_hubs` and its churning subject — _"refresh `docs/architecture.md` (251d stale; subject `src/api/` had 47 commits in window)"_ — not "improve the docs".
- **Layer 0 Partial because the doc set is fragmented:** name the orphans / islands — _"link the 6 orphan docs into the MOC; `index.md` is named but not wired (out-degree 0)"_.

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

Both SVGs are embedded as **clickable, relative links** — `[![alt](./complexity-heatmap.svg)](./complexity-heatmap.svg)` and the same for `./doc-graph.svg`. The `viewBox` lets GitHub scale them to the content column; the link lets a reader open the SVG on its own (the doc graph's hover tooltips only work when the raw SVG is opened directly — GitHub renders the inline copy as a static image). Keep the link relative, never a `raw.githubusercontent.com` URL (those are branch-specific and rot on rename). Omit a section only if its script could not generate the SVG (record the reason instead).

The plugin footer is important — it's how other engineers viewing the report in a PR discover the tool that produced it. Do not omit it.

## Step 5: Ask Whether to Open a PR

After writing the files, first **check whether a direct PR is even possible** before offering one. A user on `READ` or `TRIAGE` access can't push a branch to the target repo, so an unconditional "open a PR?" offer is infeasible and wastes a turn.

```bash
# Detect push capability. `gh` returns viewer fields for the current user.
PUSH_INFO=$(gh repo view --json viewerPermission,viewerCanPush,viewerCanAdminister,nameWithOwner 2>/dev/null || true)
# If the command failed (no remote, no gh, not a GitHub repo, unauthenticated),
# fall back to the local-branch flow with the reason - never silently assume
# push works.
```

Interpret the result:

- `viewerCanPush: true` (any of `WRITE` / `MAINTAIN` / `ADMIN` viewerPermission, or push-eligible fork access): offer the direct PR flow below.
- `viewerCanPush: false` and `viewerPermission` is `READ` / `TRIAGE`: name the constraint, then offer the fork-based PR flow ("fork `<owner>/<repo>` and open the PR from your fork?") as an alternative to "leave local". Do not offer the direct flow.
- `gh` unavailable / not a GitHub remote / not authenticated: skip both PR offers entirely and surface only the "leave local" outcome, naming the reason ("no GitHub remote detected" / "`gh` not authenticated").

Then surface the question - verbatim, picking the shape that matches the access tier:

> Wrote `.assess/assess-report.md`, `.assess/complexity-heatmap.svg`, and `.assess/doc-graph.svg` in `<repo-name>`. Want me to open a PR in this repo with these files, or leave them local for you to review first?

> Wrote `.assess/assess-report.md`, `.assess/complexity-heatmap.svg`, and `.assess/doc-graph.svg` in `<repo-name>`. You have `READ` access to `<owner/repo>`, so a direct PR isn't possible. I can fork `<owner/repo>` to your account and open the PR from there, or leave the files local for you to review.

If the user says **yes / PR** (direct flow, `viewerCanPush: true`):
1. Create a branch in the target repo: `assess/snapshot-<YYYY-MM-DD>` (use the existing worktree workflow if `<repo>-main` + `worktree/` layout is present; otherwise branch in place).
2. Stage and commit the report, the complexity heatmap, and the doc graph. Commit message: `docs: Add AI-readiness assessment + complexity and doc-navigability snapshots`.
3. Push the branch and open a PR. Title: `docs: Codebase assessment — <YYYY-MM-DD>`.

If the user says **yes / PR** (fork flow, `viewerCanPush: false` on the upstream):
1. `gh repo fork <owner>/<repo> --clone=false --remote=true` (creates the fork under the user's account and adds it as a remote named `origin` or similar; the upstream becomes `upstream` if the original was already `origin`).
2. Create the branch as above, push to the **fork** (`git push -u <fork-remote> <branch>`), and open the PR via `gh pr create --repo <owner>/<repo>` (head defaults to the fork).
3. Commit message, PR title, and body are unchanged from the direct flow.
4. **PR body must include the plugin reference at the bottom** so reviewers can install the tool that generated the report. Use this body template:

   ```markdown
   ## Summary

   Snapshot of this codebase's AI-agent readiness, complexity hotspots, and doc navigability as of <YYYY-MM-DD>.

   - **AI Readiness:** <X / 8> — <maturity-label>
   - **Hotspot leader:** `<top hotspot path>` (<loc> LOC, ccn <N>, <M> commits in window)
   - **Top lying map:** `<top stale-hub doc>` (<N>d stale, subject churn <M>)

   ## Top 3 Actions

   <paste the Top 3 Actions table from .assess/assess-report.md verbatim>

   Full report: [`.assess/assess-report.md`](./.assess/assess-report.md) (the heatmap and doc graph render inline).

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

**Start with the deterministic git-remote signal before anything else.** A GitHub / GitLab remote with issues enabled is the cheapest, most reliable tracker signal in front of you - skipping it forces the model into judgment-mode on a question that has a clear answer.

```bash
# Cheapest tracker signal: a git remote that hosts issues.
GIT_REMOTE=$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)
if [ -n "$GIT_REMOTE" ]; then
  # `gh` works against any git remote pointing at github.com (including forks);
  # hasIssuesEnabled distinguishes a code-only mirror from a real tracker.
  GH_TRACKER=$(gh repo view --json hasIssuesEnabled,nameWithOwner 2>/dev/null || true)
fi
```

Treat a non-empty `GH_TRACKER` with `hasIssuesEnabled: true` as a **detected tracker** (subject to the same write-access check as Step 5 - read-only repos still get tracking items via the user's fork or their personal tracker, not the upstream issues list). Same logic for `glab repo view --output json` on GitLab remotes.

When the deterministic signal is clear and unambiguous, use it without asking. Only fall back to judgment / multiple-signal disambiguation when the remote check is empty, ambiguous, or contradicted by something the user has told you.

Other signals - examples, not an exhaustive list, used only as fallback or to choose between equally-plausible options:

- **The user's global instructions** (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`) often state the tracker explicitly: "issues live in Linear project FOO", "we use Task Master", "Jira project ABC", etc.
- **Project-level instructions** in the target repo's `CLAUDE.md` / `AGENTS.md` / contribution docs.
- **Project files**: `.taskmaster/` directory, `.acli/` config, `.linear/` dotfiles, a Notion link in the README, etc.
- **Authenticated CLIs**: `gh auth status`, `glab auth status`, `acli` configuration, `linear` CLI tokens, anything similar.
- **Conversation history**: if the user just used a tracker, prefer that one.

The user might track work in Omnifocus, Apple Notes, a Google Doc, a Notion database, a Slack channel, or anything else. **Use judgment** on these soft signals; do not let them override a clear deterministic git-remote hit.

**Decision rules:**

1. **Deterministic GitHub/GitLab remote with issues enabled** - use that tracker without asking (subject to write-access check). This is the common case for OSS work and most personal repos.
2. **One clear soft signal** (e.g. only `.taskmaster/` present, or global CLAUDE.md says "use Linear") and no contradicting remote - use that tracker without asking.
3. **Conflicting signals** (e.g. GitHub remote + `.taskmaster/` directory + global says "use Linear") - **ask the user**. List what you saw and let them pick:
   > I see signals for Task Master (`.taskmaster/`), GitHub Issues (remote points at `<owner/repo>` with issues enabled), and Linear (your global CLAUDE.md mentions it). Which should I create the tracking items in?
4. **No clear signal** - ask:
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

After writing `assess-report.md`, write `finalize-input.json` to the transient cache and invoke `assess_finalize.py` so the wiki files reflect the score and actions you chose.

The input file lives under `.assess/.cache/` rather than directly in `.assess/` because it is a one-off LLM-authored input consumed immediately - it has no future utility and only creates noisy diffs if committed. `assess_finalize.py` reads the cache path first (and falls back to the legacy in-tree location if a prior run wrote one there), then **deletes** it on success so it cannot leak into a commit either way.

````bash
mkdir -p "$REPO_ROOT/.assess/.cache"
cat > "$REPO_ROOT/.assess/.cache/finalize-input.json" <<'EOF'
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

<!-- chat-skip:start -->
# Re-resolve the skill dir in case this runs in a fresh shell (Step 2's shell
# var won't have survived; the env var $CLAUDE_PLUGIN_ROOT will).
SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
<!-- chat-skip:end -->
<!-- chat-replace:uv-finalize -->
uv run "$SKILL_DIR/scripts/assess_finalize.py" "$REPO_ROOT"
````

This replaces:
- `log.md`'s last entry placeholder `**AI Readiness:** 0.0 / 8 ((LLM fills in))` with your actual score and maturity label.
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
