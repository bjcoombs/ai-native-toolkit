# Deterministic Assessment Snapshot: parity-fixture

_Generated <<normalized>>._

## Metrics Dashboard

- **Files scored:** 3
- **Total LOC:** 600
- **Complexity profile:** p95 LOC 200 (max 500), p95 CCN 8 (max 20)
- **Churn window:** unavailable

### Top Hotspots

| Path | LOC | CCN | Commits |
|------|-----|-----|---------|
| `src/a.py` | 500 | 20 | 5 |
| `src/b.py` | 80 | 6 | 2 |

## Keyhole Readiness

No structural concerns, 0 safe zones.

## Cross-Layer Findings (Keyhole Readiness)

_No cross-layer findings surfaced - no path crossed an axis boundary._

## Changes Since Last Run

_No prior run to compare against - this is the first recorded snapshot._

---

_Deterministic portion of the assessment: metrics, hotspots, and cross-layer findings. The 0-8 layer scores, the per-layer prose, and the Top 3 Actions priority narrative require LLM judgement and are written separately._

## How this was measured

Every check is described in [docs/what-we-measure.md](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md). A scan that did not run, or could not read its input, earns nothing and costs nothing.

### Ran (40, over 1 repository)

- `agent_ops`
- `config_drift`
- `review_reality`: not measured in 1 of 1 (no_remote)
- `delivery`: not measured in 1 of 1 (no_remote)
- `gate_cost_estimate`: not measured in 1 of 1 (no_remote)
- `coverage_gate`
- `instruction_claims`
- `claude_config`: not measured in 1 of 1 (unavailable)
- `nested_instructions`
- `agent_harness`
- `agent_environment`
- `shared_tooling`: not measured in 1 of 1 (unavailable)
- `review_automation`: not measured in 1 of 1 (git_tracking_unknown)
- `review_evidence`: not measured in 1 of 1 (no_remote)
- `ci_checks`
- `agent_building`: not measured in 1 of 1 (unavailable)
- `layer_grades`
- `instruction_placement`
- `ai_usage`: not measured in 1 of 1 (unavailable)
- `doc_claims`
- `structure`: not measured in 1 of 1 (no_source_files)
- `stats_summary`
- `promissory_markers`: not measured in 1 of 1 (unavailable)
- `observability`
- `doc_graph`
- `doc_staleness`
- `archetype`
- `diff`
- `dead_code`: not measured in 1 of 1 (unavailable)
- `ancestor_instruction_files`
- `behaviour`: not measured in 1 of 1 (unavailable)
- `documentation`
- `understanding`
- `runtime`
- `derived_findings`
- `coverage_report`: not measured in 1 of 1 (unavailable)
- `test_pressure`
- `test_focus`
- `accretion_ratchet`: not measured in 1 of 1 (unavailable)
- `gap_actions`

### Skipped (1)

- `structure_drift`: not in this run's context.
