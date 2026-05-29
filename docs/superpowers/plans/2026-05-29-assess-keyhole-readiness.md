# Assess — Keyhole Readiness (Structural Legibility) — PRD

> **For agentic workers:** Implement on a worktree branched from `main` (e.g. `assess-keyhole-readiness`). Committed home for this doc: `docs/superpowers/plans/2026-05-29-assess-keyhole-readiness.md`. Steps use checkbox (`- [ ]`) syntax for tracking. The eventual *implementation* is a **MINOR** feature (new deterministic signals + derived findings + new `run-context.json` blocks; no graph in v1; backward-compatible report shape) — bump `.claude-plugin/plugin.json` `.version` minor **in the implementing PR**, not in this planning PR (this PR is the plan only, mirroring the dismiss-false-positives PRD precedent). `skills/assess/SKILL.md` is subject to the standalone-ZIP transform (`chat-skip` / `chat-replace` markers + `scripts/standalone_skill_config.py`); keep standalone-divergent wording in config, not body prose, and rebuild/validate the ZIP.
>
> **Revised after external review:** structured evidence is the product (not a visual); the named *joins* are first-class findings; B4 reframed from "ownership" to "where understanding lives"; the third graph is demoted to an optional, deferred Phase 5; build is historical-first with static dependency analysis last.

**Type:** Feature / capability (new analysis axis)
**Priority:** High — this is the signal `/assess` is missing for its core thesis: whether a too-big-for-any-context-window repo is *structured* so an agent can change it safely through the window.
**Affected skill:** `skills/assess/` (`SKILL.md`, `scripts/assess_core.py`, `scripts/lib/`, new `scripts/lib/change_coupling.py` + `scripts/lib/structure_graph.py`, `tests/`).

---

## Problem Statement

`/assess` already measures two things well, and they never speak to each other:

1. **Where the cognitive load is** — the complexity treemap (LOC × cyclomatic complexity × git churn). This is, in the lineage, an Adam Tornhill *hotspot*.
2. **Whether the docs are navigable and honest** — the doc-graph (reachability, orphans, staleness / "lying maps").

Neither answers the question the whole toolkit exists for. Once a codebase outgrows any single context window and agents become regular contributors, the binding constraint is the **keyhole**: every actor — the agent's context window, *and* the human reviewing a diff — sees a narrow slice by construction. A change is safe only when the *unit being changed plus the contracts at its boundary* fit inside that slice. A repo can be low-complexity and well-documented and still be a **blob** — everything coupled to everything — so that no change fits the keyhole and every "locally fine" PR compounds inconsistency the next agent faithfully reproduces.

Three concrete gaps follow:

1. **No modularity / containment signal.** Nothing tells you whether handing an agent a subfolder and saying "refactor this" will stay contained or **bleed out** across the repo. Directory structure lies about this; coupling is what's real.
2. **The axes are never joined.** A *complex module with no doc summary* is a high-comprehension-cost unit with no relief. A *complex module with a stale doc* is worse than one with no doc — a lying map over dangerous territory. Today complexity and doc-state are computed in separate artifacts and never crossed.
3. **No value / liveness join.** A clean, well-documented, well-structured module can still be **dead code** — pure carrying cost, zero value. Comprehensibility says nothing about whether a thing deserves to exist.

## Conceptual Foundation

Six load-bearing decisions.

### 1. Code is a liability; behaviour is the asset

Every line carries cost — maintenance, attack surface, and the comprehension footprint it adds to every nearby change. Dead code is pure liability. The objective `/assess` should reward is therefore *the least code delivering the needed behaviour, maximally comprehensible and verifiable* — not "more/better code." This inverts the usual quality framing and makes **deletion** a first-class recommendation.

### 2. The keyhole is permanent; modularity is what makes it safe

You cannot widen the aperture (context windows grow, but real repos grow faster, and the human reviewer's aperture is the diff regardless). You can only build a codebase where any unit of change fits the keyhole *with its contracts*. Modularity stops being an aesthetic and becomes the property that decides whether AI velocity is safe or catastrophic.

### 3. Three orthogonal axes, mapping onto the existing bands

Every code unit can be scored on three independent questions. They map almost exactly onto `/assess`'s existing read / write / meta bands — this capability **extends** the layer model, it does not bolt a new one on:

| Axis | Question | Existing band / layers | What this PRD adds |
|---|---|---|---|
| Comprehensibility | Can I understand it through the keyhole? | read (L0 docs, L2/3 design) + the treemap | modularity, containment, comprehension footprint, complexity×doc join |
| Trustworthiness | Can I trust it does what it claims? | write (L2–L7) + the wiki (reputation over runs) | independent-oracle framing for tests (lighter touch) |
| Value / liveness | Does it deserve to exist? | L1 runtime legibility / liveness | reachability→runtime→value ladder, the velocity clock |

### 4. The fractal unit model

The same three sub-questions recur at every scale — function, file, package:

- **Cohesion inside** — does the unit do one thing?
- **Contract at the boundary** — narrow, explicit interface, so the inside can change without leaking?
- **Fit within the keyhole** — can I load the unit + its contracts and reason about a change completely?

`/assess` already does this at the bottom (function/file complexity). This PRD completes the fractal one and two levels up (files-in-package, packages-in-repo).

### 5. Critic, not oracle

Every signal here is a **candidate for human judgement**, never a verdict. Hard constraints:

- **Candidate-not-verdict.** The output routes the user's scarce taste to the few places that probably owe attention. It never scores elegance, which it cannot see.
- **Over-modularization is also a blob.** A hundred tiny packages with dense cross-talk is *worse* through a keyhole than a few cohesive ones. Metrics must reward cohesion-at-the-boundary, not smallness — a graph modularity score punishes confetti and blob alike, where raw size only catches blob.
- **Slop-doc guard.** "Complex module, no summary" must never become "autogenerate a summary to clear the flag." That manufactures lying maps at scale. An honest *undocumented* must score strictly safer than a hollow summary that looks like a contract and isn't. Freshness (already computed) is the guard.
- **Asymmetric delete caution.** A false "dead" is far worse than a false "alive" (deleting the disaster-recovery path that runs once a year is catastrophic). Liveness flags bias toward *keep* and always demand runtime corroboration before suggesting deletion.

### 6. Two lenses — designed vs behaved — and the disagreement is the signal

- **Static lens:** the dependency graph — structure as *designed*.
- **Historical lens:** git history — structure as it *behaves* (what actually changed together).

Where they disagree is the most interesting output: a directory that looks modular but whose commits always bleed outside it has **hidden coupling**; one that looks coupled but never co-changes is fine in practice. Crucially, the historical lens reads the *commit log, not the code*, so it is **language-agnostic** and runs on any repo immediately.

## The Signals

### A. Comprehensibility / keyhole-fit (static lens — language-specific)

- **A1. Comprehension footprint** (anchor metric). For a unit X: `size(X) + public_surface(deps of X) + surface(X exposes to dependents)`. Compare to a **keyhole budget** (a fraction of a reference window). Units over budget are the ones no agent can change completely from inside the window. Build/import a dependency graph (`grimp`/`pydeps` for Python; `madge`, `go list`, jdepend later).
- **A2. Blob vs modular.** Strongly-connected components in the package graph (cycles = definitional blob); graph **modularity score** (intra- vs inter-cluster edges).
- **A3. Contracts.** Fraction of inbound edges that land on a package's *front door* (`__init__` / public API) vs **burrow into internals**. Deep-reaching imports mean there is no real contract and refactors leak.
- **A4. Breakup candidates.** Internal sub-clusters + low internal cohesion → the package is several packages wearing one coat. The sub-clusters *are* the proposed cut-lines.

### B. Containment / natural islands (historical lens — language-agnostic, from git log)

- **B1. Change (temporal) coupling.** Files/dirs that change in the same commits. (Tornhill's metric; `code-maat` / `Hercules` compute it, or derive directly — `/assess` already parses `git log` for churn.)
- **B2. Containment ratio** (answers "will it bleed out?"). For a module M: `commits touching only files in M ÷ commits touching M at all`. High = a real island, safe to hand an agent in isolation. Low = it bleeds. This is the direct, empirical keyhole-refactor-safety metric.
- **B3. Static-vs-historical disagreement.** Cross A2/A3 with B1/B2: looks-modular-but-bleeds = hidden coupling (flag); looks-coupled-but-never-co-changes = leave it (suppress).
- **B4. Where understanding lives** (not "ownership"). Harrer's mechanism — per-file/dir authorship share from `git log --numstat` (`share = a contributor's additions ÷ total additions`) — but the AI-era question isn't *who owns it*. Classic ownership no longer answers what matters: a directory touched by hundreds of agent sessions has enormous activity and effectively **zero retained understanding**. So compute three things, not an owner: **human anchor** (has a human substantively touched/reviewed it?), **intent source** (is there an externalised spec/doc stating what it should do?), and **authorship class** of recent change (human / agent / none — detect agents from `Co-Authored-By:` trailers, bot committers, session footers this repo already stamps). High activity ∧ no human anchor ∧ no intent source = **orphaned understanding**, the worst case, and the direct feed for D2's velocity clock. (Traditional single-owner bus-factor still falls out of the same data as a secondary signal.)

### C. Complexity × doc-state join (joins the two existing artifacts)

A matrix per unit over {no doc, fresh doc, stale doc} × complexity:

- complex + fresh → the doc is the contract (good; footprint relieved).
- complex + **no doc** → flag: high load, no summary an agent can read first.
- complex + **stale doc** → **top priority**: a lying map over dangerous territory. Worse than no doc.

Doc value function: `≈ complexity_summarised × freshness`. Trivial-code docs ≈ 0; stale-complex-code docs **< 0** → recommend *delete or fix*, not preserve. Doc→code mapping stays fuzzy (path proximity, doc links/symbol mentions, module docstring) because it's a candidate signal.

### D. Value / liveness (the third axis)

- **D1. Evidence ladder.** static reachability (have it, Layer 1; lies safe-ward via reflection/DI/entrypoints) → **runtime liveness** via observability the agent can reach (strong; needs the repo to expose it — that *is* the Layer 1 test) → value attribution (human judgement; out of deterministic scope).
- **D2. The velocity clock.** Calendar age is dead as a metric — under AI velocity, "legacy" means **orphaned understanding**, which can happen on day one. Measure age in *comprehension-events*, not days. The real legacy flag is the **intersection**: high complexity ∧ owned by automation / no human anchor (B4, via blame + agent-authorship detection) ∧ no externalised intent (no spec doc) ∧ unknown/absent runtime liveness. None of the single existing metrics catch it; the intersection does.

## Derived findings (the primary output)

Individual metrics are inputs; the **joins** are the product. These named findings are computed deterministically from the blocks below and become the report's primary content. Each is a candidate for human judgement, never a verdict.

| Finding | Intersection | Recommended action |
|---|---|---|
| **Hidden coupling** | modular statically (A2/A3) ∧ strong historical bleed (B1/B2) | investigate the seam — the static boundary is lying |
| **Lying map** | high complexity ∧ stale doc (C) | fix or delete the doc; a wrong summary is worse than none |
| **Unexplained complexity** | high complexity ∧ no doc ∧ no intent source | write the missing contract (do *not* auto-generate it) |
| **Orphaned understanding** | high complexity ∧ no human anchor ∧ no intent source (B4) | assign a human anchor before further change |
| **Candidate dead weight** | high complexity ∧ no runtime evidence ∧ no intent source (D) | verify liveness, then delete if dead (bias to keep) |
| **Refactor boundary** *(positive)* | high containment ∧ low external coupling (B2) | safe to hand an agent in isolation |

**Refactor boundary** is the one positive finding, and it answers the core question directly — *can an agent safely change this area through a keyhole?* — with a yes.

## Output / Artifacts

**The structured evidence is the product; the report is its presentation layer.** The deterministic core writes new `run-context.json` blocks the LLM reasons over directly (it never sees raw data it must re-derive):

- `structure` — SCC membership, modularity score, front-door ratio, comprehension footprint *(static; later phase)*.
- `behaviour` — containment ratio, change-coupling pairs, static/history disagreement.
- `documentation` — freshness, complexity coverage, stale-doc-on-complexity.
- `understanding` — human anchor, intent source, authorship class (per B4).
- `runtime` — static reachability, runtime evidence where available.

From these the core emits the **derived findings** above plus a ranked **"where to look" list** — the few units worst across axes, each with a proposed action (split / document / anchor / verify-or-delete). The LLM fills judgement into placeholders (existing write-back pattern). Keep the report concise — no new sections that dilute attention.

**Visualization is deferred and optional — not part of v1.** A combined multi-encoding graph was explored (`docs/keyhole-readiness-mockup.svg`) and rejected for v1: cramming structure, coupling, docs, understanding and runtime into one view is itself a smell, and unlike the treemap (one variable — complexity) and doc-graph (one variable — reachability), this data is genuinely *findings-shaped, not map-shaped*. Any later visual should be a **single-variable** cut — most likely the containment/island view alone — reusing the existing doc-graph renderer, never a bespoke edge-bundling engine, and never gating the deterministic value.

## Prior Art / Build-vs-Leverage

This sits squarely in **behavioral code analysis** (Adam Tornhill). We are already in the lineage — the treemap is a Tornhill hotspot.

| Tool | License | Does | Use it how |
|---|---|---|---|
| [code-maat](https://github.com/adamtornhill/code-maat) | OSS (EPL) | change coupling, hotspots, authorship from a VCS log | reference algorithms; mostly superseded |
| [CodeScene](https://codescene.com/) | Commercial | temporal coupling, hotspots, knowledge maps, code health | prior-art benchmark, not a dependency |
| [Hercules](https://github.com/src-d/hercules) | OSS | file co-change + developer coupling matrices from full history | optional engine for B1/B2 at scale |
| [CodeCharta](https://github.com/MaibornWolff/codecharta) | OSS | visualizes metrics (imports code-maat/Tokei/CSV) | prior-art for the graph artifact |
| [software-analytics](https://github.com/feststelltaste/software-analytics) (Markus Harrer) | OSS | pandas + GitPython notebooks; **Knowledge Islands** = per-file developer ownership (>70%), circle-packing viz | technique for B4 + the circle-packing layout option |

**Lean:** compute B1/B2 (coupling, containment) **ourselves directly from `git log`** — the core already parses it for churn, it keeps the deterministic-core contract (no new runtime deps, reproducible), and it stays language-agnostic. Treat Hercules as an optional accelerator for very large histories, behind the same interface. No bespoke graph engine in v1 (see Output) — CodeCharta and Harrer's circle-packing are prior-art references should an optional single-variable visual be added later. Static A1–A4 needs a per-language dep-graph parser and is the *last* build piece (below).

## Scope / First Cut

- **Historical axis (B)** ships **language-agnostic** in v1 and dogfoods fully on *this* repo (mixed markdown + Python — git log doesn't care).
- **Static axis (A)** is **Python-first** in v1 (`grimp`-based), so it also dogfoods here; generalize per-language after.
- **Doc×complexity join (C)** ships in v1 (reuses existing complexity + doc-staleness).
- **Value/liveness (D)** is **spec'd in full** but the runtime half is **validated-elsewhere** — this repo is mostly scripts/markdown with little runtime, so D1's static-reachability + D2's blame/intent intersection dogfood here; runtime corroboration is marked as needing an observability-bearing target repo.

**Build priority** (historical-first — language-agnostic, dogfoods immediately, most directly answers the keyhole question):

1. change coupling (B1)
2. containment ratio (B2)
3. complexity × documentation join (C)
4. static/history disagreement → *hidden coupling* (B3)
5. understanding analysis (B4) → *orphaned understanding*
6. static dependency analysis (A) → comprehension footprint

Static dependency analysis is **last**: it's the only language-specific, higher-cost piece, and containment (B2) already carries the keyhole-fit question in v1 as a language-agnostic proxy. The static footprint is the richer refinement, not the v1 gate.

**Out of scope (v1):** automated value attribution (stays human); multi-language static dep-graphs; any auto-generation of docs; any automatic deletion (recommendations only); any graph rendering (deferred to optional Phase 5).

## Implementation Phases

- [ ] **Phase 0 — git-log primitives.** `lib/change_coupling.py`: parse `git log --name-only` / `--numstat`; compute B1 coupling pairs, B2 containment ratio per directory, and B4 authorship / human-anchor signals. Tests on a synthetic history. (Language-agnostic; dogfoods on this repo.)
- [ ] **Phase 1 — the doc join.** Cross existing complexity × existing doc-staleness → C; emit the *lying map* and *unexplained complexity* findings.
- [ ] **Phase 2 — understanding + runtime.** B4 human-anchor / intent-source / authorship class; D static reachability; emit *orphaned understanding* and *candidate dead weight*.
- [ ] **Phase 3 — static structure (last, language-specific).** `lib/structure_graph.py`: Python dep-graph (`grimp`), A2 SCC/modularity, A3 front-door ratio, A1 comprehension footprint vs budget; cross A × B → *hidden coupling*.
- [ ] **Phase 4 — deterministic findings + write-back.** The five `run-context.json` blocks; derived findings; ranked attention list; LLM synthesis guidance + placeholders; SKILL.md guidance (with standalone markers); MINOR version bump; rebuild/validate ZIP. **No graph generation.**
- [ ] **Phase 5 (optional, deferred) — single-variable visual.** Only if it earns its place: a containment/island view reusing the doc-graph renderer. Non-blocking; not part of the MINOR release above.

## Open Questions

1. **Keyhole budget number.** What reference window / fraction defines "fits"? Probably a tunable with a sane default, surfaced in `.assess/config.toml`.
2. **Doc→code mapping precision.** How fuzzy is acceptable before the C signal misleads? Start path-proximity + link-mention; measure false-map rate on the dogfood run.
3. **Coupling thresholds.** What containment ratio counts as "an island"? Calibrate on this repo + 1–2 reference repos before baking a default.
4. **Build vs import Hercules.** Confirm the direct `git log` path is fast enough on a large history before adding any dependency.
5. **Where the value axis's human edge sits.** Exactly which D outputs are deterministic vs surfaced-for-judgement.
6. **Human-vs-agent author attribution (B4).** How reliably can we classify a commit as agent-authored across repos? `Co-Authored-By:` trailers and session footers are high-precision but tool-specific; bot committer accounts vary. Define a conservative default (only classify when confident; otherwise "unknown") so the orphaned-understanding flag never libels a human author.
