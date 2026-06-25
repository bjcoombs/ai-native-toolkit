---
name: assess
description: "Assess a codebase's readiness for AI agent contributors using the layered contract model, and generate a complexity hotspot SVG treemap (size = LOC, hue = cyclomatic complexity, saturation = recent git churn). TRIGGER when the user types /assess, asks for an AI-readiness review, wants a complexity heatmap or hotspot map, asks 'how complex is this code?', wants migration risk triage, or asks for a codebase snapshot/report. Produces an MD report + SVG that can be opened as a PR in the target repo."
---

# AI Readiness Assessment + Complexity Hotspot

Three artefacts in one pass against a target repo:

1. **Layered contract assessment** - 0-8 score across navigability, runtime liveness, code design, linters, architecture tests, CI, coverage, review bots, and AI project management.
2. **Complexity hotspot SVG** - Codecov-style treemap of the code. Size = LOC. Colour = cyclomatic complexity. Saturation = recent git churn. Vivid red = complex AND active = riskiest to change.
3. **Doc navigability SVG** - a node-graph of the docs. Structure = connectivity (centre = entry, rim = unreachable, dashed ring = orphan); colour = staleness (vivid red = a frozen doc beside churning code = a *lying map*); size = file length. Folds navigability and the decaying-map signal into one artifact.

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

The write-side scores aren't abstract good practice - each traces to a known tendency of an AI contributor, observed across models. All three are the same defect: a self-description (the file's shape, a comment's promise, a gate's verdict) under no pressure to stay true. The deterministic core turns each into a cross-layer finding so the report names the specific files, not just the category:

- **Accretion** - an agent does what is asked, and what is asked is feature after feature; nothing in that loop asks for a refactor, so files only grow. **Now fully instrumented** via the `accretion_ratchet` finding: a file whose accumulated line count ratcheted monotonically upward across multiple commits with almost no deletion pressure (deletions below ~15% of total churn). Only top complexity/size-band files are flagged, so growth-but-simple is never noise. It surfaces on three surfaces - the `accretion_ratchet` block in `run-context.json`, the `accretion_ratchet` cross-layer finding (with its files in the attention list), and a *growth-profile* line on each flagged hotspot page (`hotspots/*.md`). The signal disclaims itself (rather than dropping the result) when the git history is degenerate - a shallow clone or squashed import has no meaningful net-delta sequence, so the block carries `reliable: false` and the hotspot line is marked as possibly incomplete.
- **Unactioned intent** - an agent records promises it never returns to keep (`TODO` / `FIXME` / "remove after migration"). Instrumented via the `unactioned_intent` finding: markers aged by the edits they survived without being kept - a lying map of intent.
- **Guardrail erosion** - under pressure to make red go green, an agent loosens the check instead of fixing the root (a suppression, a skipped test, a widened threshold), hollowing out the layers meant to protect it while they still read as Present.

### Repository archetype (not every repo is software)

The 0-8 model assumes a software repo. A **knowledge / document base** - markdown sources, an LLM-maintained wiki, a `CLAUDE.md` schema, and no application code or runtime - has no code surface for the write-side layers (L2-L7). Scoring them Missing is itself a lying score: a well-run KB reads ~2.5/8 ("Not Ready") when it is in fact well-run, penalised for not testing code it doesn't contain.

The deterministic core (`lib/archetype.py`) classifies the repo and writes an `archetype` block to `run-context.json`:

- **Detection** is a heuristic - the code-file ratio (code vs markdown) and the absence of a runtime surface (`package.json`, `pyproject.toml`, `go.mod`, `Dockerfile`, ...). A documentation-heavy *application* (lots of markdown but a real build) stays software because of the runtime-surface gate.
- **Override marker.** An `assess-archetype: knowledge-base` (or `software`) marker in any instruction file (`CLAUDE.md`/`AGENTS.md`/...) **forces or suppresses** detection, so a maintainer is never trapped by a misfire. Write it as an HTML comment, e.g. `<!-- assess-archetype: knowledge-base -->`.
- **Scoring.** For a detected knowledge base the write-side layers (2-7) are scored **N/A** (not Missing) and **excluded from the denominator**; the headline renormalises over the applicable layers (L0, L1, L8 → denominator 3) and the maturity label names the archetype and the applicable-layer count (e.g. `Knowledge Base · Solid (3 applicable layers)`). A software repo is unaffected - all 0-8 layers, denominator 8.
- **KB-maintenance signal.** `archetype.kb_maintenance` flags whether the repo documents *how the AI maintains the KB* - the [Karpathy LLM-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (immutable raw sources, the schema file as the product, an ingest workflow, query-as-filing, periodic lint/consolidation). It is both a detection signal and a scored read-side (Layer 0) quality signal; the gist is cited in the report as the best-practice pointer whether or not the workflow is documented.

This is intentionally **one** archetype (knowledge base), structured as an extensible dispatch so more are cheap to add later - not a general archetype framework (YAGNI). The `assess-layer-scorer` agent reads the block (its Step 0) and the `assess-findings` skill renders N/A layers and the renormalised headline.

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

> **Write-protected repo root?** `/assess` writes `.assess/` into `$REPO_ROOT`, and the treemap/core run as `uv` subprocesses that write there too. If your workflow keeps the repo root pristine and read-only (e.g. a `<repo>-main` clone that teammates branch from, with a hook blocking direct edits), a guard on *your* writes won't stop the subprocess - it just makes the run write into the directory you meant to protect. **Create a worktree first and run `/assess` from there.**

## Step 2: Generate the Code Heatmap + Doc Graph

This step produces **two** views of the codebase, both colour-blind-safe (OrRd ramp, no red-green):

- **Complexity heatmap** (`complexity-heatmap.svg`) - a treemap of the *code*. Size = LOC, colour = cyclomatic complexity, saturation = recent churn. Vivid red = complex AND active = "hard to change safely".
- **Doc navigability graph** (`doc-graph.svg`) - a node-graph of the *docs*. Structure shows connectivity (centre = entry point, rings = link-distance, rim = unreachable; orphans carry a dashed ring); colour shows staleness in the same grammar as the code heatmap (vivid red = a frozen doc beside churning code = a lying map); size = file length. It folds both Layer 0 doc signals - navigability and the decaying-map - into one artifact. Beyond static wikilinks and CommonMark links, it recognises Obsidian vault-native navigation - `.base` view hubs and `dataview` query blocks - as edges (resolved statically by folder / tag / frontmatter predicate), so a vault navigated by dynamic queries isn't mis-scored as orphaned. The SVG and the scored signal compute over the identical doc set: both honour the same excludes (`.assess/config.toml`).

Feed the complexity stats into the linter/complexity layer (Layer 3) and the `doc_graph` / `doc_staleness` blocks of `run-context.json` into **Layer 0** (the graph SVG is the visual; the score reads the structured blocks).

### 2a: Offer to install `scc` (one-time per repo)

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

**Offer the install only if all three are true:** `SCC_PRESENT=0`, `SCC_DECLINED=0`, and the repo looks lizard-sparse (`CODE_FILES < NONCODE_FILES` or `CODE_FILES < 10`). Otherwise skip straight to 2b (or 2c if 2b has nothing to offer).

When offering, use **AskUserQuestion** with three options (do **not** auto-install - `brew install` is a system mutation):

- **Install scc** - run the appropriate installer for the platform and continue.
- **Skip for now** - proceed with lizard only. Don't write the marker; ask again next run.
- **Skip permanently for this repo** - write `$REPO_ROOT/.assess/.no-scc` so future runs don't ask. Recommended for prompt repos or pure-docs repos where lizard-only is genuinely fine.

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

If the install fails or the platform isn't covered, fall back to lizard-only and continue - don't block the assessment.

### 2b: Offer analysis tools (capability-driven, detect-or-propose)

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

Otherwise, batch the questions into **a single AskUserQuestion call** - one question per language in `OFFERS`, three options per question:

- **Install <tool>** - run the cited install command and continue.
- **Skip for now** - proceed without the tool. Don't write a marker; ask again next run.
- **Skip permanently for this repo** - write `$REPO_ROOT/.assess/.no-<tool>` so future runs don't ask. Recommended when the language only appears in scripts/configs that don't warrant symbol-level reachability.

Phrase each question so the gain is concrete, e.g.:

> "This repo has 47 Go files. `staticcheck -checks U1000` would let `/assess` flag unreachable Go funcs as Layer 1 candidates. Install? (`go install honnef.co/go/tools/cmd/staticcheck@latest` or `brew install staticcheck`)"

When the user picks **Install <tool>**, run the platform-appropriate command from the offer. Surface any install failure as a chat message and continue - dead-code tools are degrade-don't-block (same contract as scc); a missing tool reduces Layer 1's precision but never gates the assessment.

When the user picks **Skip permanently for this repo**, write the marker:

```bash
mkdir -p "$REPO_ROOT/.assess"
touch "$REPO_ROOT/.assess/.no-<tool>"   # e.g. .no-staticcheck
```

For multi-language repos with several offers, the AskUserQuestion call lists every language in one prompt rather than serialising. The user answers once and the run proceeds with whichever tools they accepted.

#### JVM / Maven capability offers (v1)

When the deterministic core detects a Maven or Gradle project it emits a `capability_offers` block in `run-context.json` - the first proof of the capability-driven flow on a non-enumerated ecosystem. Read it after Step 2c's core run, before scoring, and act on each capability's `state`:

```bash
jq '.capability_offers' "$REPO_ROOT/.assess/run-context.json"
```

- **`liveness` → `state: "offer"`** - Maven was detected but `mvn dependency:analyze` (coarse module-level dead-dependency detection) has not run. The `consent` field names the shape: `run` (`mvn` is on PATH - offer to **run** it against the project; `dependency:analyze` needs a *compiling build*, so this is a **run-consent**, heavier than a static scan) or `install` (`mvn` absent - offer to **install** Maven first). Use **AskUserQuestion** exactly as Step 2b, phrasing the trade-off (a build that resolves dependencies and may hit the network). On accept and a `run` consent, run `mvn dependency:analyze`, capture its output, and re-run the core with the served result so the candidates feed Layer 1. On decline, the capability stays honestly named, not silently dropped.
- **`linting` / `modernization` → `state: "credited"`** - an already-configured pom.xml plugin serves it (`served_by` lists which: Checkstyle, SpotBugs, PMD, error-prone, OpenRewrite, Modernizer). **Credit it in the report; do not re-offer.**
- **Any capability → `state: "honest_degrade"`** - nothing serves it yet (module graph, linting/modernization without a configured plugin, and **all** capabilities under Gradle in v1). The block carries a `candidate_tool` and `gloss`. **Name both in the report's Layer 1/Layer 3 prose** ("module-graph analysis is unserved here; `jdeps` would provide it"). Honest-degrade is a deliverable - surfacing the candidate is the point.

**Boundary (v1).** Only Maven liveness is *served*. Module graph (`jdeps`), linting, and modernization honest-degrade; Gradle honest-degrades entirely. The `candidate_tool` values are deterministic defaults - you may propose a better-fitting ecosystem tool at runtime (the detect-or-propose latitude above); that choice is human-judged, not CI-tested. CI tests only **signal consumption**: given a tool's output, the scorecard feeds correctly.

### 2c: Run the treemap

Run the bundled treemap script alongside the deterministic core - see the chained block below.

The script prints a one-line summary (file count, lizard vs scc coverage, churn window chosen, top 5 biggest files). The stats sidecar contains percentiles (p50/p95/max for LOC, CCN, churn) and ranked lists of the top 10 files by hotspot score, raw CCN, and raw LOC. Both feed the report.

**Dependencies:** the script uses PEP 723 inline metadata (`lizard`, `squarify`, `matplotlib`, `numpy`). `uv` resolves them on first run.

**Build artifacts and generated code are filtered by default.** The script excludes two classes of files:

- **Build artifacts**: `main.dart.js`, Flutter canvaskit/skwasm runtime bundles (`canvaskit.js`, `skwasm*.js`), `*.min.js`, `*.bundle.js`, `*.chunk.js`, `*.map`, sourcemaps, service workers, and files under `node_modules/`, `dist/`, `build/`, `.next/`, `.nuxt/`, `.output/`, `coverage/`, etc.
- **Generated code**: protobuf bindings (`*.pb.go`, `*_grpc.pb.go`, `*.pb.gw.go`, `*.connect.go`, `*_pb.ts`, `*_pb.d.ts`, `*_pb2.py`, `*.pb.cc`, `*.pb.h`), Go generators (`*.gen.go`, `wire_gen.go`, `zz_generated_*.go`, `bindata.go`), .NET source generators (`*.designer.cs`, `*.g.cs`), Dart/Flutter codegen (`*.freezed.dart`, `*.g.dart`, `*.gr.dart`).

Full list in `complexity-treemap.py`'s `EXCLUDE_DIRS` and `EXCLUDE_FILE_PATTERNS`. If you specifically want to score these (e.g., to visualise how much of the repo is generated), pass `--include-artifacts`.

**Dominance warning.** If a single file still holds >30% of total scoreable LOC after filtering (the threshold compiled bundles typically cross), the script prints a warning to stderr identifying the file. When you see this, the right next step depends on *why* the file is large:

- **Compiled bundle or committed build output** (`main.dart.js`, a bundled JS file, etc.): surface in the report's "Hotspot snapshot" section as "`<file>` holds X% of LOC and is likely a build artifact - recommend adding to `.gitignore` and re-running." Add a Top 3 Action of the same shape.
- **Intentionally-tracked reference data** (regulatory raw exports, vetted-context corpora, seed datasets, large CSV/JSON reference tables): the file is *meant* to be in git but isn't source code. The fix is `--exclude`, not `.gitignore`. Recommend the user persist the rule in `.assess/config.toml` so subsequent runs apply it automatically (see "Custom excludes" below). Do not push toward `.gitignore` in this case.
- Either way, do NOT skip the rest of the assessment - the layered scan still produces useful signal.

**Custom excludes for vetted-context / reference data.** When the repo intentionally tracks large non-source files, two mechanisms extend the built-in defaults (the built-ins always apply; both layers are additive). **The same excludes apply across every scan** - the heatmap, the doc-navigability graph, the doc-staleness pass, and the liveness scan all honour the same list, so "this is reference data, not source" is a single statement, not a per-layer toggle:

1. **CLI flag** `--exclude PATTERN` (repeatable, ad-hoc). A plain string is matched as a directory name; a glob is matched against the basename. The flag exists on the treemap script for one-off runs:

   ```bash
   <!-- chat-replace:treemap-exclude-example -->
   uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" --exclude regulatory-raw --exclude vetted-context --exclude '*.csv'
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

The script's own output directory `.assess/` is excluded automatically - prior runs' `run-context.json` and SVGs never feed the next run's heatmap, the doc graph, or the dead-code scan. Test fixtures under `**/tests/fixtures/**` are likewise excluded automatically - they are inputs that exercise the scanners (sample `CLAUDE.md` / monolithic-instruction files), not navigational docs or live code, so counting them would inflate the orphan rate and depress the Layer 0 navigability read.

**Raw-source-tree exclusion.** The read-side metrics (orphan rate, reachability, broken links) describe the **curated wiki** - the navigable layer an agent traverses. A repo can also track trees of raw, machine-extracted source documents (a disclosure / SAR export of hundreds of `.msg`/`.pdf`/`.docx` files converted to markdown). Those are immutable raw sources: they legitimately have no inbound wiki links and carry machine-extracted, non-navigational links (`mailto:`/`tel:`/footer URLs), so counting them as orphans / broken links inflates the figures and masks the curated signal. The doc graph auto-detects such subtrees - threshold-based: a large subtree that is almost entirely link-isolated *and* carries the machine-extraction fingerprint (`lib/raw_source.py`) - and **excludes** them from the headline metrics, reporting each excluded tree + its file count (`doc_graph.excluded_raw_trees`) and the raw layer's own figures separately (`raw_source_doc_count` / `raw_source_orphan_rate` / `raw_source_broken_links`). A repo with no raw-source tree is unaffected. The detection reuses the link graph already built, so there is no second parse.

**If the script fails** (no `uv`, no scoreable files, etc.), record the error in the report under "Hotspot snapshot" as "could not be generated - <reason>" and continue with the layered assessment. The treemap is additive; assessment still runs without it.

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

Either SVG is additive: if a script fails (no `uv`, no scoreable files, no docs), record "could not be generated - <reason>" in the report and continue. The doc graph shares its data with the deterministic core's `doc_graph` / `doc_staleness` blocks, so even when the SVG can't render, Layer 0 still scores from `run-context.json`.

Now `$REPO_ROOT/.assess/run-context.json` contains the structured data you need for the prose sections. Read it before writing the report.

The `plugin_version` field in `run-context.json` tells you which plugin version produced this run. Surface it at the top of the report (e.g., "Generated by `/assess` v1.8.0") so readers can spot it if a stale cached version of the plugin produced unexpected output.

### 2d: Offer the bounded mutation pass (opt-in)

The default core run is read-only - it never mutates or runs code, so `test_pressure` carries the cheap hollow-test heuristics and mutation-config detection but no survivor data. The decisive Layer 1 signal (would a test actually fail if the code were wrong?) needs a bounded mutation pass, which mutates source and *runs* the suite. That is consent-gated, so offer it here rather than running it silently.

The `test_focus` block is the single source of focus targets - the risky files that most need test work, already cross-joined from hotspot risk, coverage, and the hollow-test heuristics. Read it; don't recompute it:

```bash
jq '.test_focus' "$REPO_ROOT/.assess/run-context.json"
```

<!-- chat-replace:mutation-offer-intro -->
**Only when `test_focus.entries` is non-empty** (at least one non-clean target) is there anything to deepen - if it is empty, every hot file is already covered-and-pinned (or there are no hotspots), so skip straight to Step 3. When it has entries, follow the same detect-or-offer-install pattern as Steps 2a/2b: first detect a mutation tool, then ask the user whether to run the bounded pass.

<!-- chat-skip:start -->
**Detect a mutation tool for the repo's language.** Mirror the Step 2b heuristic - `mutmut` for Python, `stryker` for TS/JS - and check PATH plus the permanent-decline marker:

```bash
# Pick the candidate tool by the dominant focus-file language (Python -> mutmut,
# TS/JS -> stryker). Fall back to mutmut when the focus files are mixed/Python.
FOCUS_FILES=$(jq -r '.test_focus.entries[].path' "$REPO_ROOT/.assess/run-context.json" | head -5)
case "$FOCUS_FILES" in
  *.ts|*.tsx|*.js|*.jsx) MUT_TOOL=stryker ;;
  *) MUT_TOOL=mutmut ;;
esac
command -v "$MUT_TOOL" >/dev/null 2>&1 && MUT_PRESENT=1 || MUT_PRESENT=0
[ -f "$REPO_ROOT/.assess/.no-$MUT_TOOL" ] && MUT_DECLINED=1 || MUT_DECLINED=0
```

**Offer with AskUserQuestion** (skip the offer entirely when `MUT_DECLINED=1`). Phrase the trade-off concretely - a bounded pass that mutates source and runs the suite over up to 5 focus files, time-boxed. Three options, same shape as Steps 2a/2b:

- **Run mutation analysis** - run the bounded pass on the focus files (installing `$MUT_TOOL` first if `MUT_PRESENT=0`, exactly as Step 2b installs a dead-code tool: run the platform-appropriate install, surface any failure as a chat message, and fall back to no-mutation on failure - never block).
- **Skip for now** - continue with the cheap read intact. Don't write a marker; ask again next run.
- **Skip permanently for this repo** - `touch "$REPO_ROOT/.assess/.no-$MUT_TOOL"` so future runs don't ask.

**On accept (tool available):** run the opt-in mutation pass, then regenerate the heatmap with the survivor overlay. The core re-run reads the `test_focus` targets itself, runs `scan_test_pressure(..., opt_in=True)` scoped to them, and rewrites the `test_pressure` block in `run-context.json` in place:

<!-- chat-skip:end -->
```bash
<!-- chat-skip:start -->
# Re-resolve the skill dir (Step 2's shell var won't survive a fresh shell;
# the env var $CLAUDE_PLUGIN_ROOT will).
SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
<!-- chat-skip:end -->
# 1. Re-run the test-pressure scan with the bounded mutation pass enabled
<!-- chat-replace:uv-core-mutation -->
uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT" --opt-in-mutation

# 2. Regenerate the heatmap with the survivor overlay so covered-but-unpinned
#    files get hatched and stop reading as safe green
<!-- chat-replace:uv-treemap-overlay -->
uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json" --test-pressure "$REPO_ROOT/.assess/run-context.json"
```

<!-- chat-skip:start -->
With no mutation data the `--test-pressure` flag is a silent no-op, so the overlay regeneration is harmless even if the pass produced nothing.

**On decline or no tool available:** continue with the cheap read intact - the assessment is complete without it. State in the report that the deep mutation pass was **not** run and why (declined, or no mutation tool for the language), so the Layer 1 read is honest about its depth rather than implying the focus files were proven well-tested.
<!-- chat-skip:end -->

## Step 3: Score the Layers

The deterministic core has written the data bus (`.assess/run-context.json`). Assigning each layer Present / Partial / Missing is judgement-heavy work that benefits from a fresh context window applying the layer methodology - so it runs as a dedicated unit, not inline here.

<!-- chat-replace:layer-scorer-delegate -->
Spawn the `assess-layer-scorer` agent (`subagent_type: "assess-layer-scorer"`), passing `REPO_ROOT`. It reads `.assess/run-context.json`, scores every layer, and returns the 0-8 score, the per-layer verdicts with evidence, and the maturity label. Hold that scorecard for Step 4.

## Step 3.5: Read Cross-Run Context

Before the report is written, check what changed since the last run (the findings-writer renders this into the report's diff section):

```bash
jq '.diff, .diff_detail' "$REPO_ROOT/.assess/run-context.json"
```

If `prior` was None (first run), skip this section in the report.

**Check `diff_reliable` first.** When `run-context.json` has `diff_reliable: false`, the prior snapshot came from a different (or unstamped) plugin version (`diff_version_note` explains it) - file-filter differences across versions surface phantom "graduated"/"new" transitions that didn't really happen. **Suppress the "What Changed Since Last Run" section** in that case and instead note one line: _"Diff suppressed - prior snapshot predates version stamping or used a different file filter; comparison resumes once two runs share a plugin version."_ Otherwise, populate the section:

- **Graduated** (good): list paths from `diff_detail.graduated` - hotspots that left the top list
- **Regressed** (bad): list paths from `diff_detail.regressed` with their `ccn_delta` / `commits_delta`
- **New** (watch): list paths from `diff_detail.new`
- **Persistent** (structural debt if N runs in a row): list paths from `diff_detail.persistent`

The wiki files at `.assess/index.md` and `.assess/hotspots/*.md` are already updated by `assess_core.py` - you don't need to write them. You only write the prose summary in `assess-report.md`.


## Step 4: Write the Report

Assembling `.assess/assess-report.md` - the scorecard, the snapshots, the verbatim cross-layer findings, the lying signals, and the mandatory Top 3 Actions - is a reusable, mostly-deterministic procedure. It runs as a sub-skill.

<!-- chat-replace:findings-delegate -->
Use the assess-findings skill, handing it the scorecard the layer-scorer returned. It assembles `.assess/assess-report.md` from the data bus plus the scorecard: the verbatim findings section, the lying signals, and the Top 3 Actions (the attention list is mandatory). Then continue to Step 7.5.

## Step 7.5: Finalize the wiki (required)

After writing `assess-report.md`, write `finalize-input.json` to the transient cache and invoke `assess_finalize.py` so the wiki files reflect the score and actions you chose.

The input file lives under `.assess/.cache/` rather than directly in `.assess/` because it is a one-off LLM-authored input consumed immediately - it has no future utility and only creates noisy diffs if committed. `assess_finalize.py` reads the cache path first (and falls back to the legacy in-tree location if a prior run wrote one there), then **deletes** it on success so it cannot leak into a commit either way.

````bash
mkdir -p "$REPO_ROOT/.assess/.cache"
cat > "$REPO_ROOT/.assess/.cache/finalize-input.json" <<'EOF'
{
  "score": 6.0,
  "maturity_label": "Solid",
  "denominator": 8,
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
      "effort": "small",
      "files": [".golangci.yml"],
      "first_step": "Add 'cyclop' with max-complexity: 15 under linters",
      "done_when": "golangci-lint run passes with the rule active; no new suppressions added",
      "scope_fence": "Only .golangci.yml; do not edit source files to chase pre-existing violations"
    }
  ]
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

The optional `denominator` field is **8** for a software repo (the default when omitted) or the applicable-layer count for a detected archetype (3 for a knowledge base - see "Repository archetype" above). `assess_finalize.py` renormalises both the `log.md` AI-Readiness line and the badge over it, so a KB reads `2.5 / 3` rather than a misleading `2.5 / 8`.

`assess_finalize.py` also refreshes `.assess/badge.json` (shields.io endpoint schema) from your score and maturity label - the live README badge. When offering the PR (assess-pr), include the embed snippet if the repo's README has no badge yet:

```markdown
![AI-readiness](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2F<owner>%2F<repo>%2F<default-branch>%2F.assess%2Fbadge.json)
```

The `actions` array mirrors the report's Top 3 Actions table one-to-one and **must carry every table row** - `rank`, `action`, `done_when`, and `scope_fence` are required per entry (`layer`, `effort`, `files`, `first_step` recommended). `assess_finalize.py` writes it to `.assess/actions.json`, the *durable* machine-readable contract: unlike this input file (consumed and deleted), `actions.json` persists so an executing agent - including a smaller, cheaper model - can pick up the work with its exit criteria and fences intact, without parsing the report's markdown.

Without this step, the `log.md` placeholders above carry forward forever. Hotspot pages you don't supply actions for keep a neutral pointer (`This file is flagged but outside this run's Top 3. See the report's Top 3 Actions, or run a focused /assess pass for file-specific guidance.`) rather than an unfinished-work placeholder - a flagged-but-not-Top-3 page reads as intentional.

The hotspot_actions dict should include at minimum the files mentioned in your Top 3 Actions. You can include more if you have specific suggestions for them; any file you omit keeps the neutral pointer.


## Step 8: End-of-Run Offers

With the report written and the wiki finalized, run the end-of-run offers - open a PR, track the Top 3 Actions, freeze the assessment into a CI gate - and the tool-feedback prompt. This is a reusable procedure (akin to `pr-review-merge`), so it runs as a sub-skill.

<!-- chat-replace:pr-delegate -->
Use the assess-pr skill. It runs the three offers (PR, issue tracking, freeze-into-CI) in order, then the tool-feedback prompt, reading the written `.assess/assess-report.md` artifact - notably mutating the Top 3 Actions table's `Issue` column in place when the user creates tracking items.
