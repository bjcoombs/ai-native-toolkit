# skills/assess/tests

Test suites for the `/assess` deterministic engine. Tests live here, co-located with
the scripts they pin. When a source module changes, its test file is expected to change
in the same commit - this is intentional co-change, not accidental coupling.

## Test/source co-change seam

The suites below are expected to move with the engine. A reviewer seeing
`test_doc_graph.py` and `lib/doc_graph.py` in the same diff is looking at the normal
edit cycle, not a layering violation. Keep tests co-located.

The two highest-frequency co-change pairs in the git history are:

- **`test_assess_core.py` / `assess_core.py`** - the orchestrator and its end-to-end
  harness. Every time the core adds a signal or changes the `run-context.json` schema,
  both files move.
- **`test_doc_graph.py` / `lib/doc_graph.py`** - the navigability graph is the
  foundation for Layer 0 and feeds both the staleness and the understanding analysis,
  so its contract tests are touched on most doc-analysis changes.

---

## Suite / source mapping

### Orchestrator suites

| Suite | Pins |
|---|---|
| `test_assess_core.py` | `scripts/assess_core.py` - end-to-end orchestrator; drives `build_run_context` without running lizard/scc |
| `test_assess_finalize.py` | `scripts/assess_finalize.py` - LLM write-back; placeholder substitution in `log.md` and `hotspots/*.md` |
| `test_assess_gate.py` | `scripts/assess_gate.py` - CI regression gate; complexity and containment threshold checks and exit codes |
| `test_assess_report.py` | `scripts/assess_report.py` - deterministic report renderer; template substitution, section renderers, conditional fallbacks |
| `test_emit_workflow.py` | `scripts/assess_emit_workflow.py` - CLI wrapper for the frozen-harness workflow emitter; default derivation and arg parsing |
| `test_decomposition_parity.py` | `scripts/assess_core.py` + `scripts/assess_report.py` - parity harness; guards that the deterministic pipeline produces byte-for-byte identical output after the Part 3 SKILL.md decomposition |
| `test_complexity_treemap.py` | `scripts/complexity-treemap.py` - build-artifact filter, plugin version stamp, and stats-sidecar enrichment (heavy deps are stubbed) |

### lib/ suites

| Suite | Pins |
|---|---|
| `test_doc_graph.py` | `lib/doc_graph.py` - doc link-graph, link parsing, orphan detection, connectivity, MOC validation, doc->code edges |
| `test_keyhole_signals.py` | `lib/keyhole_signals.py` - integration barrier; derivation of the five run-context blocks and the six named derived findings from mocked upstream signal outputs |
| `test_change_coupling.py` | `lib/change_coupling.py` - B1 change-coupling pairs, B2 containment ratio, B4 authorship; synthetic git histories built in tmp dirs |
| `test_coupling_analysis.py` | `lib/coupling_analysis.py` - B3 static-vs-historical disagreement; hidden-coupling, bleeding-module, and refactor-boundary classification with mocked inputs |
| `test_doc_complexity_join.py` | `lib/doc_complexity_join.py` - Signal C: doc_value formula, slop-doc guard, threshold behaviour; mocked complexity-stats and staleness inputs |
| `test_doc_staleness.py` | `lib/doc_staleness.py` - doc->code association (base-doc, parallel docs/, code links, repo-wide fallback) and churn-relative staleness ratios |
| `test_structure_graph.py` | `lib/structure_graph.py` - A1 footprint additivity, A2 SCCs and Q range, A3 front-door vs burrow, A4 cut-lines, graceful degradation |
| `test_understanding_analysis.py` | `lib/understanding_analysis.py` - B4 human anchor + intent source, velocity clock (D2), orphaned-understanding classification; both pure-logic (mocked) and git-integration variants |
| `test_liveness_scan.py` | `lib/liveness_scan.py` - dead-code tool output parsers, observability rungs, graceful degradation when tools are absent |
| `test_test_pressure.py` | `lib/test_pressure/` - mutation tier output parsing, cheap heuristics (test/source ratio, assertion density, gap signal) |
| `test_ci_workflow.py` | `lib/ci_workflow.py` - template substitution (version, branch, tool steps), literal-dollar escaping, YAML well-formedness |
| `test_stats_diff.py` | `lib/stats_diff.py` - hotspot transition classification (graduated, regressed, new, persistent) and sidecar loading |
| `test_wiki_writer.py` | `lib/wiki_writer.py` - wiki file rendering (index, log, hotspot pages) and HotspotEntry / LogEntry dataclass behaviour |
| `test_git_commit_info.py` | `lib/git_churn.py` (`git_commit_info`) - commit snapshot with SHA/timestamp for staleness warnings |
| `test_instruction_bloat.py` | `lib/agent_instructions_grader.py` - bloat penalty, skills-delegation credit, conservative thresholds |

### Infrastructure suites

| Suite | Pins |
|---|---|
| `test_smoke.py` | `lib/__init__.py` - confirms the lib package is importable and `__version__` is set |
| `test_golden_baseline.py` | `tests/golden.py` + dogfood fixtures - guards the regression baseline scaffolding (fixture completeness, normalization idempotency, loader correctness) used by `test_decomposition_parity.py` |

---

## Running the suite

```bash
# From skills/assess/ - avoids ~7 phantom git-commit failures from global git config
GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest pytest tests/ -v
```

The phantom failures are a local-only artifact of global git commit-template or hook
configuration. They do not appear in CI.
