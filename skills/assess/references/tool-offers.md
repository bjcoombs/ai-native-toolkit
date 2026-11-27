# Phase 1 tool offers (Steps 2a and 2b)

Steps 2a and 2b of `SKILL.md`: the `scc` check, the batched analysis-tool install offer, and the JVM / Maven capability offers.

## Contents

- 2a: Detect `scc` need (feeds Phase 1)
- 2b: Phase 1 - batched analysis-tool install offer (capability-driven, detect-or-propose)
  - JVM / Maven capability offers (v1)

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
