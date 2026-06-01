# Assess Dogfooded - Phase 0 Baseline & Seam Analysis

> Companion to the PRD `docs/superpowers/plans/2026-05-31-assess-dogfooded.md`. This is the Phase-0 dogfood record: the golden baseline captured against this repo, and the empirical containment/coupling analysis of `skills/assess/SKILL.md`'s sections that validates the Part-3 decomposition seams. Task 8 (decomposition) relies on the line ranges and seam verdicts below.

## 1. Golden baseline (regression target for Part 3 parity)

A full `/assess` run was captured against this repo at commit `5901e9d` (plugin `v1.19.2`), score **6.0 / 8 (Solid)**.

Artifacts regenerated freshly into `.assess/`: `run-context.json`, `complexity-heatmap.svg`, `doc-graph.svg`, `assess-report.md`, plus the deterministic wiki (`index.md`, `log.md`, `hotspots/*.md`).

Normalized golden fixtures committed under `skills/assess/tests/fixtures/golden/` (path confirmed against the existing `fixtures_dir` convention in `tests/conftest.py`):

- `run-context-baseline.json` - the deterministic data bus, the **primary** parity target.
- `assess-report-baseline.md` - the LLM-prose report, a structural reference (prose is not byte-deterministic; the run-context is).

**Normalization.** Volatile fields are masked with the sentinel `<<normalized>>` so parity tests survive version bumps and new commits. The transform lives in `skills/assess/tests/golden.py` (`normalize_run_context`, `normalize_report`) and was used to *produce* the goldens - so a parity test applies the identical transform to a fresh run and compares, and the only differences that can fail are real divergences in the deterministic computation. Masked fields:

- run-context: `plugin_version`, `prior_plugin_version`, `run_date`, `diff`, `diff_detail`, `diff_reliable`, `diff_version_note`, `prior_stats_exists` (cross-run state, prior-sidecar-dependent), and `measured_commit.{head_sha,head_short,committed_date,subject,dirty,upstream,behind}`. `measured_commit.available` is preserved (structural).
- report: the `_Generated ..._` provenance line and the `Measured at commit` bullet.

`skills/assess/tests/test_golden_baseline.py` guards the baseline (7 tests): all expected blocks present (`derived_findings`, `attention`, `behaviour`, `documentation`, `understanding`, `runtime`, `structure`), six keyhole findings in fixed order, normalization idempotent and non-mutating. `pyproject.toml` `pythonpath` gained `"tests"` so test modules can import the shared `golden` helper.

## 2. SKILL.md section map (1234 lines)

| Section | Lines | Approx LOC | Role |
|---|---|---|---|
| Frontmatter + "truth-pressure" model intro | 1-38 | 38 | preamble |
| Step 1 - repo root + output dir | 39-62 | 24 | preflight |
| Step 2 - heatmap + doc-graph (2a scc install, 2b dead-code-tool install, 2c run treemap + core) | 63-263 | 201 | core invocation + interactive tool-install offers |
| Step 3 - Scan Each Layer (Layers 0-8) | 264-705 | 442 | per-layer scoring (judgement) |
| Cross-layer derived findings (keyhole) | 706-723 | 18 | findings read |
| Step 3.5 - Read Cross-Run Context | 725-743 | 19 | diff prep |
| Step 4 - Score and Write the Report (template, snapshots, scorecard, lying signals, top 3, additional, strengths) | 744-959 | 216 | report rendering + findings rendering + scoring write-up |
| Step 5 - Ask Whether to Open a PR | 961-1034 | 74 | end-of-run offer |
| Step 6 - Track Top 3 in issue tracker | 1035-1155 | 121 | end-of-run offer |
| Step 7.5 - Finalize the wiki (required) | 1156-1196 | 41 | deterministic write-back |
| Step 7 - Tool Feedback (optional) | 1197-1234 | 38 | end-of-run offer |

## 3. The coupling measurement (the tool's own lens, applied to SKILL.md)

The tool scores **containment** (do edits stay local? - its `refactor_boundary` finding) and **coupling** (do things change together across a boundary? - its `hidden_coupling` finding). Applied to SKILL.md's prose sections, the "symbols" that cross section boundaries are: (a) `jq` reads of `run-context.json` keys (the deterministic data bus), and (b) prose cross-references to another section by name ("Step N", "the Top 3 Actions table", "the Lying Signals section").

### 3a. The data bus is clean and partitioned (good coupling)

Seven `jq` reads of `run-context.json`, each landing in exactly one candidate unit - units pull their inputs from the bus, not from each other:

| Line | Keys read | Unit |
|---|---|---|
| 277 | `.instruction_files`, `.instructions_grade`, ... | layer-scorer (L0) |
| 334 | `.doc_graph`, `.doc_staleness`, `.stale_hubs` | layer-scorer (L0) |
| 362 | `.dead_code`, `.observability` | layer-scorer (L1) |
| 711 | `.derived_findings`, `.attention` | findings-writer |
| 730 | `.diff`, `.diff_detail` | report-renderer (diff section) |
| 798 | `.measured_commit` | report-renderer (snapshot) |
| 1202 | `.anomalies` | pr-and-issues (feedback) |

This is the decoupling interface the decomposition relies on: `assess_core.py` writes the bus once (zero model tokens), and every downstream unit reads its slice. A decomposed unit needs no reference to a sibling's internals - only to its own keys.

### 3b. Cross-unit prose coupling is low and concentrated (the change-amplification risk)

| From | To | Where | Nature |
|---|---|---|---|
| layer-scorer (L3) | orchestrator (Step 2) | L473 "only if Step 2 produced it" | data-bus (sidecar presence) - benign |
| layer-scorer (L6) | report-renderer | L647 "See the Lying Signals section of the report" | prose - 1 ref |
| report-renderer | pr-and-issues | L891 "Issue column is filled in by Step 6"; L1140 edits the Top 3 table | shared artifact (`assess-report.md`) - the tightest seam |
| pr-and-issues (Step 6) | pr-and-issues (Step 5) | L1037, L1059 "same write-access check as Step 5" | intra-unit - benign |
| orchestrator (Step 7.5) | orchestrator (Step 2) | L1179 "Step 2's shell var won't have survived" | intra-unit - benign |
| Step 2b | Step 2a | L125, L127 "same install-offer pattern as Step 2a" | intra-unit - benign |

The within-Layer-0 "see ... below" references (L288 alias, L292 sensitive-content, ancestor-cascade) all stay inside layer-scorer - **high internal cohesion**, exactly what a clean agent boundary wants.

## 4. Seam verdicts (validates PRD 3a/3b)

| Unit | Lines | Containment | Mechanism | Verdict |
|---|---|---|---|---|
| **orchestrator** | 1-62, 218-263, 725-743, 1156-1196 | High | thin skill (stays `SKILL.md`) | **Clean cut.** Preflight + core-run sequence + finalize; talks to scripts and the bus, not to sibling prose. Trim target ~150 lines holds. |
| **layer-scorer** | 264-705 | High | **agent** | **Clean cut.** 442 lines of judgement, high internal cohesion, reads three bus slices, 1 outbound prose ref. Directly analogous to a `/huddle` hat reading its own methodology. |
| **pr-and-issues** | 961-1034, 1035-1155, 1197-1234 | High | **sub-skill** | **Clean cut.** ~233 lines; Steps 5/6/7 reference each other but reach back into scoring only via the report artifact. Akin to `pr-review-merge`. |
| **report-renderer** | 744-864, 725-743 | Moderate | **script** (Part 2a) | **Clean once findings are deterministic.** Shares the report artifact with findings-writer and the `Issue` column with pr-and-issues; the template skeleton is otherwise mechanical. |
| **findings-writer** | 706-723 + 865-880 + 881-928 | **Low (as written)** | sub-skill or deterministic script | **NOT a clean cut yet.** It straddles Step 3 (emit) and Step 4 (render: Lying Signals + Top 3). Extracting it before Part 1 shatters one unit across two - the 3b "don't split into confetti" guard. **Part 1's deterministic surfacing must land first** (move rendering into the core), then this becomes a clean script boundary. |

## 5. Conclusions for Part 3

1. **The seams the PRD proposed are real.** Four of five units (orchestrator, layer-scorer, pr-and-issues, report-renderer) cut cleanly along the `run-context.json` data bus with minimal cross-unit prose coupling.
2. **The mechanism choices in 3b hold empirically.** layer-scorer is judgement-heavy and cohesive → **agent**; pr-and-issues is a reusable deterministic procedure → **sub-skill**; report-renderer is mechanical templating → **script**.
3. **The sequencing in the PRD is load-bearing, not cosmetic.** `findings-writer` is the one entangled unit, and the entanglement is exactly what Part 1 removes. Decomposing (Part 3) before giving the findings teeth (Part 1) would force a confetti split. **Keep the order Part 1 → Part 2 → Part 3.**
4. **The tightest residual seam is the `Issue` column** shared between report-renderer (writes `—`) and pr-and-issues (fills `#N`). The decomposition must keep that contract explicit - the report template owns the column; pr-and-issues mutates it in place via the `assess-report.md` artifact, not via shared prose.
5. **Parity test target:** assert the decomposed pipeline reproduces `run-context-baseline.json` after `normalize_run_context`. The report's prose is LLM-authored and not byte-stable; the deterministic bus is the invariant to pin.
