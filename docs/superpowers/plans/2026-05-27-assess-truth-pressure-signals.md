# Assess — Truth-Pressure Signals (Read-Side Foundation) — PRD

> **For agentic workers:** Implement on a worktree branched from `main` (e.g. `assess-truth-pressure-signals`). Suggested home for the committed copy of this doc: `docs/superpowers/plans/2026-05-27-assess-truth-pressure-signals.md`. Steps use checkbox (`- [ ]`) syntax for tracking. This is a **MINOR** feature (new readiness signal + reframed scoring, backward-compatible report shape) — bump `.claude-plugin/plugin.json` `.version` minor in the same PR. `skills/assess/SKILL.md` is subject to the standalone-ZIP transform (`chat-skip` / `chat-replace` markers + `scripts/standalone_skill_config.py`); keep all standalone-divergent wording in config, not body prose, and rebuild/validate the ZIP.

**Type:** Feature / model enhancement
**Priority:** High — closes the largest blind spot in the readiness model (the "read side").
**Affected skill:** `skills/assess/` (`SKILL.md`, `scripts/assess_core.py`, `scripts/lib/`, `tests/`).

---

## Problem Statement

The `/assess` layered contract model measures, almost entirely, whether the agent can be trusted to **write** — types, linters, architecture tests, CI, coverage, review bots (Layers 1–6) all constrain *output* quality, and Layer 7 is the self-improvement loop. The only "read-side" signal (can the agent form a *true picture* before it acts) is Layer 0 (agent instructions), and it covers only **static** intent.

Two failures follow, both invisible to the current model:

1. **Liveness blindness.** A subsystem can pass every write-side layer — compiles, typed, linted, tested, reviewed — and still be **dead**: the data feeding it stopped, the consumer (e.g. a mobile app) was re-pointed elsewhere, but the code remains. Finding the code does not mean it is still used. Tests carry truth-pressure on *correctness*, not on *relevance*. An agent (or a human new to the repo) treats the dead subsystem as live and builds research on a false foundation.

2. **Presence ≠ maintenance.** The model rewards artefacts for *existing* (a README, a wiki, an AGENTS.md). But an unmaintained doc is a **stale comment at scale** — it actively points the agent at moved buildings. Presence without maintenance is a liability, not an asset.

Concrete evidence: the `meridian` reference repo is wiki-structured (strong static navigability) **and** has observability instrumented — yet the observability "is not used well." Under the current model it would score well on presence and the blindness above would go unflagged. That gap is exactly what this PRD closes.

## Conceptual Foundation (read this before implementing — it changes how you score)

Across the whole model, the real signal is never **presence**. It is whether a thing is under **active pressure to stay true**:

- Tests keep **behaviour** honest (CI fails when wrong).
- Retros / feedback loops keep the **process** honest (Layer 7 already scores whether retros are *actively carried out*, not merely present).
- Maintenance keeps **docs** honest (a wiki tracked against code churn).
- Telemetry / liveness keeps **relevance** honest (is this code actually exercised).

So: **AI-readiness is the degree to which the codebase's self-descriptions are kept honest, not the degree to which scaffolding exists.** Every layer is a truth-pressure check in disguise. This reframing is load-bearing for the scoring logic below — score artefacts on *maintenance pressure*, not existence.

The stack has three bands:

- **Read-side foundation** — can the agent form a true picture? (Layer 0 static navigability + the new Layer 1 runtime legibility)
- **Write-side enforcement** — can the agent be trusted to produce good output? (the existing contract/gate layers)
- **Meta** — does the system keep itself honest over time? (the feedback layer — stays the capstone, last)

## Technical Context

- The **readiness score is produced by the LLM** following `SKILL.md` (`assess_core.py` sets `readiness_score=0.0  # LLM produces the layered score`). The deterministic core supplies *inputs* the LLM scores against (instruction grades, complexity stats, churn, diffs) via `.assess/run-context.json`.
- `scripts/complexity-treemap.py` **already computes per-file git churn** (heatmap saturation = recent churn) and percentiles in `complexity-stats.json`. The doc-staleness metric below reuses this machinery — do not reinvent churn.
- `scripts/assess_core.py` already grades instruction files **with freshness** (`_file_freshness_days`, `grade_instructions(..., freshness_days=...)`) and distinguishes *null* (no file → "create one") from *F* (stale/poor file → "fix it"). Extend this distinction to docs/wiki.
- `skills/assess/SKILL.md` is the authoritative source; the standalone ZIP is derived via the marker transform. Respect the pipeline; rebuild and validate.

## Layer Position — Layer 1, Renumber 7 → 8 (DECIDED 2026-05-27)

The position is **not a preference; it falls out of the sort principle.** Sort the layers by **dependency** — what must hold for the next layer to *mean anything* — and the order is fixed by the firm edges:

`L0 Navigability → L1 Liveness → {write-side enforcement} → feedback (capstone)`

- **Navigability (L0)** depends on nothing — the entry; you must read the map before anything.
- **Liveness (L1)** depends on L0 (you must orient to a thing before asking if it's still real) and **gates the entire write-side** — enforcing types/lint/coverage on dead code is wasted at best, misleading at worst.
- **Write-side enforcement** depends on the read-side: contracts/gates only mean something once you can trust *what you're reading is real and current*.
- **Feedback** depends on a working enforced system to improve — genuinely last.

So Runtime Legibility is **Layer 1, derived.** This also settles "why renumber rather than just append as Layer 8": **appending would place liveness *after* the write-side, violating a real dependency edge** — telling the agent to trust enforcement signals before confirming the code is live (the cricket failure, baked into the model). Append is a dependency violation, not a lower-churn alternative. **Decided 2026-05-27: adopt the renumber — the model moves from 7 layers to 8 (score `X / 8`), feedback stays last.** Position (L1) and numbering are both settled, not open.

Recommended layout (score becomes `X / 8`):

| New | Layer | Band |
|-----|-------|------|
| 0 | Agent Instructions **& Navigability** (enriched) | read-side |
| **1** | **Runtime Legibility / Liveness (NEW)** | read-side |
| 2 | Code Design (was 1) | write-side |
| 3 | Linters (was 2) | write-side |
| 4 | Architecture Tests (was 3) | write-side |
| 5 | CI Pipeline (was 4) | write-side |
| 6 | Coverage Gates (was 5) | write-side |
| 7 | Automated Code Review (was 6) | write-side |
| 8 | AI Project Management / Feedback (was 7) — **capstone, stays last** | meta |

Renumbering touches the report template, maturity bands, and the wiki — all regenerated per run, so no migration of historical `.assess/` reports is needed (they are snapshots). Note the partial order: the sort *within* the write-side block (L2–L7) is **convention, not dependency** — those layers are largely siblings, so their internal sequence is a presentation choice; don't over-invest in it. The one firm constraint is the read → write → meta band order.

Rescaled maturity bands for `/8`:

| Score | Level |
|-------|-------|
| 0–2 | Not Ready |
| 3–4 | Basic |
| 5–6 | Solid |
| 7–8 | AI-Native |

## Solution Requirements

1. **Enrich Layer 0** to score *navigability under maintenance pressure*, not artefact presence.
2. **Add Layer 1 — Runtime Legibility / Liveness**, with two verification tiers (deterministic + observability), scoring agent-usable liveness signal.
3. **Score stale-but-present at or below absent** for misleading artefact classes (docs/wiki). Missing makes the agent go look; confidently-stale makes it navigate fast to a wrong, current-looking conclusion.
4. **Frame the model around truth-pressure** in `SKILL.md` (the three bands; "kept honest, not present").
5. **Deterministic inputs where possible** — reuse existing churn machinery; degrade gracefully where a language tool is unavailable; never block the assessment.
6. The behavioural navigation test (actually running an agent to reach a correct conclusion) is **out of scope / Phase 2** (see below) — too expensive and architecturally larger; this PRD ships the deterministic + scan-based signals.

## Proposed Changes

### Part A — Enrich Layer 0 (Agent Instructions & Navigability)

- [ ] Broaden the orientation scan beyond README/ADR/API specs to recognise **navigability artefacts**: a Map-of-Content (MOC) / index note, a linked-doc graph (Karpathy-pattern LLM wiki — cross-referenced markdown), `AGENTS.md`, and **repo skills** (`.claude/skills/`, `skills/`, etc.).
- [ ] **Build and score the doc link-graph — navigability is a graph property, so measure it as one (deterministic, traditional-technique verification, not a presence check).** Parse all docs for `[[wikilinks]]` *and* `[text](relative/path)` links into a directed graph. Core: `networkx` + a parser handling **both** link forms (resolve relative targets to real files, strip `#anchors`, handle name collisions — `Path(link).stem` alone is too naive); `obsidiantools` as an optional fast-path when an Obsidian-style vault is detected. Derive signals:
  - **Centrality / PageRank** → hubs / MOCs identified automatically (no filename guessing) — the load-bearing docs.
  - **Orphans** (no inbound links) → unreachable-by-traversal docs = navigability gap; flag.
  - **Connectivity / reachability (the headline navigability score):** a *good* graph is all linked together — one connected component, fully reachable from the entry points (README / `AGENTS.md` / top MOC), no orphan islands. That is precisely what makes it navigable by human *and* agent (both traverse links; fragmentation strands both). Measure orphan-rate and island-count; this is the primary Layer-0 navigability metric.
  - **MOC validation (declared vs structural):** cross-check *claimed* MOCs (by filename/convention — `index.md`, MOC notes) against the graph — a real MOC manifests as a **hub** (high out-degree/centrality, linking its cluster). A declared MOC that isn't a structural hub is **named but not wired** → finding. The graph *shows whether the MOCs are set up correctly*, rather than trusting the convention.
  - **Doc→code edges** → links pointing at code files are a first-class doc→code association source, complementing the nearest-ancestor rule (Part E) — same parse pass yields both.
- [ ] **Weight maintenance-pressure by centrality:** a stale *hub* (high PageRank) is the most dangerous lying map — everything routes through it — so **centrality × staleness** is the priority signal feeding both the heatmap and the Layer 0 score.
- [ ] Add a deterministic **doc-staleness metric**: reuse `complexity-treemap.py`'s git churn to compute, for doc/wiki/MOC/`AGENTS.md` files, churn over the same window, expressed **relative to the churn of the code they sit beside** (per-dir or overall). Emit a `doc_staleness` block into `run-context.json` (e.g. per-doc `last_commit_days`, `code_churn_in_window`, ratio). Absolute age alone is not the signal — a 2-year-old doc beside 2-year-old code is fine; a 2-year-old doc beside a 200-commit module is a decaying map.
- [ ] Encode the **truth-pressure ordering of navigation aids** in the scoring guidance: executable aids that run in CI (skills, doctests) > tests > linked-doc graph / MOC > prose. Executable aids fail loudly when they rot; prose rots silently. Weight maintained/executable aids higher.
- [ ] Scoring rule: a wiki/MOC/AGENTS.md scores **Present** only when *maintained* (churn tracks code); **Partial/Missing** when stale relative to code churn, with stale-but-present flagged as actively misleading (mirror the existing null-vs-F remediation split).
- [ ] **Score modular structure with maintained per-module base docs as the navigability target (size-weighted) — not an optional nicety.** A module/package owning a base doc (resolvable by the nearest-ancestor rule in Part E) is what makes a large codebase navigable to humans *and* deterministically mappable for an agent — the convergence: AI-readiness here *is* good modular architecture, which large codebases need anyway. **Weight by size:** do not penalise a small/single-purpose repo (modularity would be overhead); flag a large or monolithic codebase lacking modular decomposition + per-module base docs as a Layer 0 gap, remediation "decompose into modules; add a maintained base doc per module." The per-module docs are held to the same maintenance-pressure standard — a stale base doc is still a lying map, so the target is *modular + maintained*, never just "more docs."

### Part B — New Layer 1: Runtime Legibility / Liveness

- [ ] **Deterministic tier (traditional, cheap):** add a best-effort dead-code / reachability scan that flags **intra-repo candidate-dead code** — unused exports / unreferenced files / unreachable symbols. Use a language-appropriate tool where present (e.g. `vulture` Python, `knip`/`ts-prune` TS, `staticcheck`/`deadcode` Go, `cargo`/clippy hints Rust) and degrade gracefully otherwise. Emit `dead_code_candidates` into `run-context.json`. **State the hard limit in the report:** static reachability proves "nothing in *this* repo calls it," never "no external consumer calls it." Cross-boundary liveness is the next tier.
- [ ] **Observability tier (the decisive one) — three rungs, and the top rung is the real signal:**
  1. **Instrumented** — telemetry is emitted: OpenTelemetry, Prometheus, Datadog/APM, structured logging, production-coverage tooling. Necessary, not sufficient.
  2. **Discoverable** — an `OBSERVABILITY.md` / runbook tells the agent *where* runtime truth lives (log locations, dashboards-as-code, SLOs, data-freshness monitors). Orients, but doesn't grant access.
  3. **Reachable — does the agent actually have the skills/tools to view it?** An agent-*invokable* path to runtime state: an MCP server over logs/metrics/traces (`.mcp.json`), a **repo skill** that tails logs or queries metrics, a documented-and-runnable CLI the agent can call. This is the decisive rung — without it the agent *knows* telemetry exists but cannot *use* it, so liveness stays unverifiable in practice. A Grafana no agent can query from stops at rung 1. (Note these reachability tools are the same class as nav skills — executable, so they carry truth-pressure: a `view-logs` skill breaks loudly if it can't reach the logs.)
  Scan signals: `.mcp.json` exposing observability, repo skills named for logs/metrics/traces, runbooks containing runnable query commands. **Boundary:** assess sees what the *repo provides* toward agent-reachability; it cannot fully know the agent's live environment — score what the repo makes reachable, and say so in the report.
- [ ] **Scoring (by the rungs above):**
  - **Present** — reaches **rung 3** (agent can actually read logs/metrics/traces via an invokable tool/skill/MCP), with dead-code hygiene (dead code removed or flagged for removal).
  - **Partial** — instrumented, maybe discoverable, but **not agent-reachable** (rungs 1–2 — the `meridian` case: telemetry exists, the agent can't use it), or candidate-dead code present and unflagged.
  - **Missing** — no runtime instrumentation; liveness unknowable from the repo.
- [ ] Encode the **liveness asymmetry** in guidance: *no traffic is strong evidence of dead; some traffic is weak evidence of live* (could be a healthcheck, a zombie client, or a once-a-year batch).
- [ ] Encode the **honest limit**: some liveness facts live only in people's heads ("kept for Legal, not wired up") — unreachable by code *or* telemetry. The signal's job is therefore to make the agent treat **code-presence as a hypothesis, not a fact** ("X is PRESENT; liveness NOT confirmed; needs telemetry or a named human"), never to assert liveness.

### Part C — Framing, scoring, scaffolding

- [ ] Add a short **truth-pressure framing** to the top of `SKILL.md`'s readiness section (the three bands; "kept honest, not present"). Keep standalone-divergent wording in config if any.
- [ ] Apply the renumbering (decided 2026-05-27 — see "Layer Position" above) across `SKILL.md` layer headers, the scoring matrix, the report template, and the maturity table (`/7` → `/8`, rescaled bands above).
- [ ] Update `Top 3 Actions` guidance so a Partial on Layer 1 produces the *correct* remediation — e.g. "make existing observability agent-queryable" for the `meridian` case, **not** "add observability."

### Part D — Tests & release

- [ ] Unit tests for the new deterministic inputs: `doc_staleness` computation (`tests/test_assess_core.py` or a new module) and `dead_code_candidates` emission, including graceful-degradation paths (tool absent → empty result, no crash).
- [ ] Fixture coverage: a stale-doc fixture (old doc + churny code) and a fresh-doc fixture; an instrumented-but-human-only fixture vs an agent-queryable fixture.
- [ ] Respect the standalone pipeline: rebuild (`bash scripts/build-standalone-skills.sh assess`), confirm no orphan markers and standalone wording intact; run `cd scripts && uv run --with pytest pytest -v` (skill-local tests too).
- [ ] MINOR version bump in `.claude-plugin/plugin.json`.

### Part E — Docs staleness heatmap (second treemap, inverted scoring)

The current heatmap (`complexity-treemap.py`) scores **code**: size = LOC, hue = cyclomatic complexity, saturation = recent churn; vivid red = complex AND active = risky to change. Docs need a **separate heatmap with inverted semantics**, because a doc's risk profile is the opposite of code's — the danger isn't "complex and changing," it's **frozen while its subject moves** (the decaying map). Shares data with Part A's doc-staleness metric.

- [ ] Add a second treemap (reuse the squarify/matplotlib machinery) over doc files (`.md`, MOC/wiki/`AGENTS.md`) → `.assess/docs-staleness-heatmap.svg` + stats sidecar.
- [ ] **Encoding — same visual grammar, inverted meaning:**
  - **Size** = **link-degree / PageRank centrality** from the Part A doc-graph (how load-bearing the doc is — hubs/MOCs are biggest, so a stale hub dominates the map); fall back to doc size (lines/bytes) only where the graph is unavailable.
  - **Hue** = doc staleness (days since the doc's last commit); red = stale.
  - **Saturation** = churn of the **code the doc describes** (subject's recent activity); vivid = subject actively moving.
  - **Vivid red = a frozen doc whose subject is churning = the most dangerous lying map.** A stale doc beside dead code is pale (harmless); the *ratio* is what colours it.
- [ ] **Doc→code association — directory hierarchy first (deterministic where the repo co-locates):** resolve by the **nearest-ancestor base-doc rule** — each code file is described by the nearest base doc walking up its directory ancestry; each base doc's subject = its subtree *down to the next base doc* (same nearest-match logic as `CODEOWNERS` / `.gitignore`). Exact in repos following a base-doc-per-module convention (e.g. Meridian). Recognise a configurable set of base-doc names (`README.md`, `<dir>.md`, `index.md`, `AGENTS.md`, MOC notes); exclude boilerplate (`LICENSE`, `CHANGELOG`, `CONTRIBUTING`). Ordered fallbacks when co-location is absent: (b) a parallel `docs/` tree mirroring the code tree by name (`docs/payments.md` → `src/payments/`); (c) explicit relative-path links the doc contains; (d) repo-wide code churn as the subject baseline. Report which method was used per doc, and the limits.
- [ ] **Association-derivability is itself a Layer 0 signal.** If the doc→code map can be derived from structure (clean hierarchy/convention), that's positive navigability; if docs float disconnected from the code they describe, that's a maturity gap. Surface "% of code under a base doc" / "% of docs that map to code" and let it inform the Layer 0 score.
- [ ] Update `SKILL.md` Step 2 to run the second treemap and reference **both** heatmaps in the report (code = "risky to change", docs = "actively misleading"); feed the docs-heatmap stats into **Layer 0** scoring the way the code heatmap feeds the linter/complexity layer.
- [ ] **Why two heatmaps, not one:** code red = "hard to change safely"; docs red = "actively misleading." Same size/hue/saturation grammar, opposite risk model — which is exactly why they need separate scoring rules and separate artefacts.

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `skills/assess/SKILL.md` | Truth-pressure framing; enriched L0; new L1; renumber; scoring matrix + maturity bands; Top-3 remediation guidance |
| Modify | `skills/assess/scripts/assess_core.py` | Emit `doc_staleness` and `dead_code_candidates` into `run-context.json`; reuse churn |
| Modify/Create | `skills/assess/scripts/lib/` (e.g. `doc_graph.py`, `doc_staleness.py`, `liveness_scan.py`) | Doc link-graph (`networkx`: centrality, orphans, reachability, doc→code edges); doc-churn-vs-code-churn; dead-code/observability scans |
| Modify | `skills/assess/scripts/complexity-treemap.py` | Expose per-file churn for doc files if not already reusable |
| Create | `skills/assess/scripts/docs-staleness-treemap.py` | Second treemap: docs staleness, inverted encoding → `docs-staleness-heatmap.svg` |
| Create/Modify | `skills/assess/tests/` | Tests + fixtures for the new inputs and degradation paths |
| Modify | `scripts/standalone_skill_config.py` | Any standalone-divergent wording relocated out of `SKILL.md` body |
| Modify | `.claude-plugin/plugin.json` | MINOR version bump |

## Success Criteria

- `/assess` on a repo with a stale wiki beside churny code scores Layer 0 **Partial/Missing** and names the stale docs — not Present.
- `/assess` on `meridian` scores Layer 0 **strong** and Layer 1 **Partial**, with the remediation "make existing observability agent-queryable" (not "add observability").
- `/assess` flags intra-repo candidate-dead code in the report, with the explicit caveat that cross-boundary liveness needs telemetry/a human.
- The report frames readiness as truth-pressure (kept honest, not present) and shows the renumbered `/8` model with feedback last.
- A second `docs-staleness-heatmap.svg` renders: a frozen doc beside churning code shows vivid red; a stale doc beside dead code shows pale; node size reflects link-graph centrality (a stale hub dominates).
- The doc link-graph identifies hubs/MOCs by centrality and flags orphan docs; reachability from entry points (README/`AGENTS.md`) is reported, and a stale hub surfaces as a top finding.
- New deterministic inputs are tested, degrade gracefully when language tools are absent, and never block an assessment.
- ZIP rebuild clean; `pytest` green; MINOR version bumped.

## Risk Assessment

Moderate. Mostly additive: new deterministic inputs + `SKILL.md` content + a renumber. Primary risks: (1) the dead-code/reachability scan is language-specific and brittle — mitigate by treating it as best-effort, degrading to "not assessed" rather than failing; (2) renumber drift across `SKILL.md`, report template, and tests — mitigate with a sweep and the success-criteria checks; (3) standalone/CLI wording divergence — mitigate via the existing ZIP forbidden-string test plus a rebuild-and-read; (4) the docs-heatmap doc→code association is heuristic — degrade to co-location / repo-baseline and state the limits in the report. No customer-facing runtime affected; the skill is a read-only assessor.

## Out of Scope / Phase 2

- **Behavioural navigability test** — actually running an agent against a known question to verify it reaches a *correct, current* conclusion (the gold-standard "verify, don't assert" check). High value but expensive and a larger architectural change; spec separately once the deterministic signals land.
- **Live telemetry integration** — querying real production metrics/traces during assessment (vs detecting that an agent-queryable channel exists). Detection ships now; querying is Phase 2.
