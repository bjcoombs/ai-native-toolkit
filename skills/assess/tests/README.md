# skills/assess/tests

Test suites for the `/assess` deterministic engine. Tests live here, co-located with
the scripts they pin. When a source module changes, its test file is expected to change
in the same commit - this is intentional co-change, not accidental coupling.

## Test/source co-change seam

The suites below are expected to move with the engine. A reviewer seeing
`test_doc_graph.py` and `lib/doc_graph.py` in the same diff is looking at the normal
edit cycle, not a layering violation. Keep tests co-located.

The three highest-frequency co-change pairs in the git history are:

- **`test_assess_core.py` / `assess_core.py`** - the orchestrator and its end-to-end
  harness. Every time the core adds a signal or changes the `run-context.json` schema,
  both files move.
- **`test_complexity_treemap.py` / `complexity-treemap.py`** - the heatmap CLI and its
  filter, version-stamp and sidecar tests.
- **`test_doc_graph.py` / `lib/doc_graph.py`** - the navigability graph is the
  foundation for Layer 0 and feeds both the staleness and the understanding analysis,
  so its contract tests are touched on most doc-analysis changes.

---

## Suite / source mapping

Every `test_*.py` file in this directory has a row below.

### Orchestrator suites

| Suite | Pins |
|---|---|
| `test_assess_core.py` | `scripts/assess_core.py` - end-to-end orchestrator; drives `build_run_context` without running lizard/scc; tests of code that moved into a `lib/` module sit in that module's suite below |
| `assess_core_helpers.py` | repo-seeding helpers shared by `test_assess_core.py` and the lib suites split from it (not a test module) |
| `test_assess_finalize.py` | `scripts/assess_finalize.py` - LLM write-back; placeholder substitution in `log.md` and `hotspots/*.md` |
| `test_assess_gate.py` | `scripts/assess_gate.py` - CI regression gate; complexity and containment threshold checks and exit codes |
| `test_assess_report.py` | `scripts/assess_report.py` - deterministic report renderer; template substitution, section renderers, conditional fallbacks |
| `test_emit_workflow.py` | `scripts/assess_emit_workflow.py` - CLI wrapper for the frozen-harness workflow emitter; default derivation, arg parsing, path filters and the path-filter default |
| `test_decomposition_parity.py` | `scripts/assess_core.py` + `scripts/assess_report.py` - parity harness; guards that the deterministic pipeline produces byte-for-byte identical output after the Part 3 SKILL.md decomposition |
| `test_complexity_treemap.py` | `scripts/complexity-treemap.py` - build-artifact filter, plugin version stamp, stats-sidecar enrichment, the render summary, CLI argument and scope checks, and the `plural()` count helper (heavy deps are stubbed) |

### lib/ suites

| Suite | Pins |
|---|---|
| `test_doc_graph.py` | `lib/doc_graph.py` - doc link-graph, link parsing (unit level: `test_doc_links.py`), orphan detection, connectivity, MOC validation, doc->code edges |
| `test_doc_links.py` | `lib/doc_links.py` - link pass called directly: wikilink and markdown-link edges, ghosts, directory links, machine-link counts, code-span skipping, ambiguity, fence stripping |
| `test_doc_graph_layout.py` | `lib/doc_graph_layout.py` - radial shells, node classification, broken-link ghost grouping |
| `test_keyhole_hidden_coupling.py` | `lib/keyhole_signals.py` - containment by directory, static-modularity projection, behaviour block, coupled pairs on hidden_coupling, structure-drift seams |
| `test_keyhole_blocks.py` | `lib/keyhole_signals.py` - documentation, understanding and runtime run-context blocks |
| `test_keyhole_attention.py` | `lib/keyhole_signals.py` - `assemble_findings` in `FINDING_ORDER`, candidate dead weight, attention ranking and tie-break, `FINDING_MODES` |
| `test_keyhole_render.py` | `lib/keyhole_signals.py` - findings markdown, keyhole summary, prescribed actions |
| `test_keyhole_untrusted_hotspot.py` | `lib/keyhole_signals.py` - E1 untrusted hotspots, E2 test-to-code mapping |
| `test_keyhole_accretion.py` | `lib/keyhole_signals.py` - accretion_ratchet finding |
| `test_keyhole_override_contradicts.py` | `lib/keyhole_signals.py` - override_contradicts_signals finding |
| `test_keyhole_suppression.py` | `lib/keyhole_signals.py` - degenerate churn, config excludes, archive paths, pruning of renamed or deleted paths |
| `keyhole_helpers.py` | shared `integrate()` fixtures for the `test_keyhole_*.py` files (not a test module) |
| `test_change_coupling.py` | `lib/change_coupling.py` - B1 change-coupling pairs, B2 containment ratio, B4 authorship; synthetic git histories built in tmp dirs |
| `test_coupling_analysis.py` | `lib/coupling_analysis.py` - B3 static-vs-historical disagreement; hidden-coupling, bleeding-module, and refactor-boundary classification with mocked inputs |
| `test_doc_complexity_join.py` | `lib/doc_complexity_join.py` - Signal C: doc_value formula, slop-doc guard, threshold behaviour; mocked complexity-stats and staleness inputs |
| `test_doc_staleness.py` | `lib/doc_staleness.py` - doc->code association (base-doc, parallel docs/, code links, repo-wide fallback), churn-relative staleness ratios, and the lying-map rule that counts only the subject's commits after the doc's last edit |
| `test_structure_graph.py` | `lib/structure_graph.py` - A1 footprint additivity, A2 SCCs and Q range, A3 front-door vs burrow, A4 cut-lines, graceful degradation |
| `test_understanding_analysis.py` | `lib/understanding_analysis.py` - B4 human anchor + intent source, velocity clock (D2), orphaned-understanding classification; both pure-logic (mocked) and git-integration variants |
| `test_liveness_scan.py` | `lib/liveness_scan.py` - dead-code tool output parsers, observability rungs, graceful degradation when tools are absent |
| `test_test_pressure.py` | `lib/test_pressure/` - mutation tier output parsing, cheap heuristics (test/source ratio, assertion density, gap signal) |
| `test_ci_workflow.py` | `lib/ci_workflow.py` - template substitution (version, branch, tool steps, path filters), path-filtered workflow detection, literal-dollar escaping, YAML well-formedness, and the `actions/checkout` pin matching this repo's gate workflow and `README.md` |
| `test_stats_diff.py` | `lib/stats_diff.py` - hotspot transition classification (graduated, regressed, restructured, new, persistent; restructured when the worst function falls while summed complexity rises) and sidecar loading |
| `test_wiki_writer.py` | `lib/wiki_writer.py` - wiki file rendering (index, log, hotspot pages) and HotspotEntry / LogEntry dataclass behaviour |
| `test_git_commit_info.py` | `lib/git_churn.py` (`git_commit_info`) - commit snapshot with SHA/timestamp for staleness warnings |
| `test_instruction_bloat.py` | `lib/agent_instructions_grader.py` - bloat penalty, skills-delegation credit, conservative thresholds |
| `test_accretion_ratchet.py` | `lib/accretion_ratchet.py` - the accretion-ratchet scanner (files that only ever grow) |
| `test_accretion_ratchet_threshold.py` | `lib/accretion_ratchet.py` - regression: the caller's `--deletion-threshold` is the cut applied |
| `test_agent_instructions_grader.py` | `lib/agent_instructions_grader.py` - heuristic grading of agent instruction files |
| `test_agent_ops.py` | `lib/agent_ops.py` - agent-operations guardrail scan (Layer 8 evidence) |
| `test_anomaly_detector.py` | `lib/anomaly_detector.py` - anomaly detection on run output |
| `test_archetype.py` | `lib/archetype.py` - repository archetype detection |
| `test_assess_config.py` | `lib/assess_config.py` - the working-notes keys in `.assess/config.toml` |
| `test_badge.py` | `lib/badge.py` - the shields.io endpoint badge and its producers |
| `test_config_drift.py` | `lib/config_drift.py` + `lib/gh_cli.py` - committed GitHub-config snapshots against the live setting, and the shared `gh` helper |
| `test_context_blocks.py` | `lib/context_blocks.py` - keyhole, stale-hub, liveness, coverage-report, accretion, structure-drift and exclusion/pruning blocks in run-context |
| `test_coverage_gate.py` | `lib/coverage_gate.py` - enforced coverage-threshold detector |
| `test_coverage_report.py` | `lib/coverage_report.py` - coverage-report parser |
| `test_dart_capabilities.py` | `lib/dart_capabilities.py` - Dart linting and liveness capability entries |
| `test_dart_complexity.py` | `lib/dart_complexity.py` - the approximate Dart per-function scanner |
| `test_decline_markers.py` | `lib/decline_markers.py` - decline-marker provenance and re-offer on a major bump |
| `test_diff_reliability.py` | `lib/diff_reliability.py` - cross-run diff reliability: version stamps, tool-version changes, schema changes |
| `test_doc_provenance.py` | `lib/doc_provenance.py` - provenance-aware staleness of generated docs |
| `test_evidence_check.py` | `lib/evidence_check.py` - deterministic re-check of scorer evidence |
| `test_scorer_evidence.py` | `lib/evidence_check.py` - the layer scorer's structured evidence contract |
| `test_gap_actions.py` | `lib/gap_actions.py` - the deterministic Top 3 gap candidates |
| `test_gate_cost.py` | `lib/gate_cost.py` - the CI gate's Actions cost estimate |
| `test_generated_files.py` | `lib/generated_files.py` - generated-file header sniff and long-line detector |
| `test_git_churn.py` | `lib/git_churn.py` - churn-degeneracy detector |
| `test_instruction_claims.py` | `lib/instruction_claims.py` - verifying claims in agent instruction files |
| `test_instruction_files.py` | `lib/instruction_files.py` - instruction-file discovery and grading, alias grade inheritance, broken references, sensitive content, ancestor cascade (through `build_run_context`) |
| `test_interactivity.py` | `lib/interactivity.py` - the non-interactive consent contract |
| `test_jvm_capabilities.py` | `lib/jvm_capabilities.py` - the capability-driven JVM offer flow |
| `test_mutation_cap.py` | `lib/mutation_cap.py` - the `test_pressure` block shape and the Layer 6 mutation-not-run cap |
| `test_ownership_parser.py` | `lib/ownership_parser.py` - ownership-map parser |
| `test_promissory_markers.py` | `lib/promissory_markers.py` - stale TODOs, suppressions and skips |
| `test_raw_source.py` | `lib/raw_source.py` - raw-source subtree detection |
| `test_review_reality.py` | `lib/review_reality.py` - review-automation evidence from merged PRs |
| `test_run_wiki.py` | `lib/run_wiki.py` - hotspot pages, `index.md` rows and graduation records for one run |
| `test_scan_registry.py` | `lib/scan_registry.py` - the declared scan table and the loop that runs it |
| `test_sibling_tests.py` | `lib/sibling_tests.py` - the one sibling-test resolver |
| `test_structure_drift.py` | `lib/structure_drift.py` - Tier 0 path-existence structure-drift signal |
| `test_test_focus.py` | `lib/test_focus.py` - `compute_test_focus` and mutation scope |
| `test_vault_queries.py` | `lib/vault_queries.py` - vault-native navigation query parser |
| `test_wiki_state.py` | `lib/wiki_state.py` - first-flagged dates and their rekeying through the rename map |

### Infrastructure suites

| Suite | Pins |
|---|---|
| `test_smoke.py` | `lib/__init__.py` - confirms the lib package is importable and `__version__` is set |
| `test_golden_baseline.py` | `tests/golden.py` + dogfood fixtures - guards the regression baseline scaffolding (fixture completeness, normalization idempotency, loader correctness) used by `test_decomposition_parity.py` |
| `test_golden_svg_render.py` | `scripts/complexity-treemap.py` + `scripts/doc-graph-svg.py` - runs the real renderers and locks their colour encoding |
| `test_doc_graph_svg.py` | `scripts/doc-graph-svg.py` - the SVG honours the same excludes as the scorer; node encoding, labels and layout; title and summary counts with singular/plural nouns and no em dash; CLI argument checks |
| `test_action_contract.py` | `action.yml` (repo root) - the composite AI-readiness gate action |
| `test_no_contributions_scan.py` | `skills/assess-pr/SKILL.md` (relative to the repo root) - extracts and runs the marked no-contributions bash block |
| `test_uninstall.py` | `references/uninstall.md` + `scripts/assess_core.py` - run-context pointer, doc completeness, and the uninstall offer |
| `test_scope.py` | `/assess <path>` monorepo scoping across `lib/doc_graph.py`, `lib/git_churn.py`, `lib/badge.py` and `lib/wiki_writer.py` |
| `test_log_supersede.py` | `lib/wiki_writer.py` - `log.md` entry replacement and re-chain across core runs and finalize |
| `test_hotspot_orphan_invariant.py` | `lib/wiki_writer.py` - executable invariant for the hotspot wiki |
| `test_self_architecture.py` | `scripts/lib/` - the inward-only layering contract and the lib `README.md` entry check |
| `test_self_dogfood.py` | the whole deterministic core - it must obey the signals it computes |

---

## Fixtures

`conftest.py` provides three fixtures: `fixtures_dir` (the path to `fixtures/`),
`tmp_assess_dir` (an empty `.assess/` with a `hotspots/` subdirectory) and `git_repo`
(a throwaway repo plus a `commit_fn` that can backdate author and committer time). At
import time it points `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` at the null device, so
ambient commit signing or hooks cannot break the git-backed tests.

`fixtures/` holds data, not tests; `pyproject.toml` excludes it from collection:

| Fixture | Used by |
|---|---|
| `golden/` | `golden.py`, `test_decomposition_parity.py`, `test_golden_baseline.py` - the run-context and report baselines |
| `golden-doc-repo/`, `golden-svg-repo/` | `test_golden_svg_render.py` - small repos the real renderers draw |
| `hollow_test_repo/`, `honest_test_repo/` | `test_test_pressure.py` - weak versus real tests |
| `mutmut-junitxml.xml` | `test_test_pressure.py`, `test_complexity_treemap.py` - mutation output |
| `good_instructions.md`, `bad_instructions.md`, `monolithic_instructions.md`, `lean_with_skills/` | the instruction grader and bloat suites |
| `coverage.xml`, `lcov.info` | `test_coverage_report.py` |
| `prior_stats.json`, `current_stats.json` | `test_stats_diff.py` |
| `maven_project/` | `test_jvm_capabilities.py` |
| `structure_drift/` | `test_structure_drift.py` |

## Markers

`no_cover` (declared in `pyproject.toml`) pauses pytest-cov tracing for one test. It
guards wall-clock budgets: the Dart scanner's 1s pathological-input test in
`test_dart_complexity.py` would otherwise miss its budget under `--cov`. Use it rather
than widening a budget to fit the tracer.

---

## Running the suite

```bash
# From skills/assess/
uv run --with pytest --with pyyaml pytest tests/ -v

# With line coverage, as CI runs it (fails below fail_under in pyproject.toml)
uv run --with pytest --with pytest-cov --with pyyaml pytest -v \
  --cov=scripts --cov-report=term --cov-report=xml:coverage.xml
```

`pyyaml` lets the structural guards in `test_action_contract.py` parse YAML; without it
they skip. `conftest.py` neutralises global git config, so the suite needs no
`GIT_CONFIG_GLOBAL=/dev/null` prefix. Scripts run outside pytest, such as
`assess_core.py`, still read global config.
