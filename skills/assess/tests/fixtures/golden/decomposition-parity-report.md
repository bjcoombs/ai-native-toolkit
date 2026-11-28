# Deterministic Assessment Snapshot: parity-fixture

_Generated <<normalized>>._

**[Step](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#step) 2 of 4: supervised agent PRs.**

Holding: 1 of 2 Step 3 gates held · 0 of 7 [measured catalogue conditions](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#catalogue-coverage) met, 0 [verified](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#evidence-grade).

Blocks Step 3: CI does not turn red when a check breaks.

Since last run: gate changes [not known](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#not-measured); closed actions not known (no baseline is committed yet, and this run's result cannot be recorded as one).

Start here: [Make CI turn red when a check breaks](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md#step-gates)

Done when: the next `/assess` run reads that CI turns red on a broken check.

_New to these words? The [glossary](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md) explains [Step](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#step), [gate](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#gate), [practice](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#practice), [level](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#level), [evidence grade](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#evidence-grade) and [truth tag](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md#truth-tag) in a sentence or two each._

## Catalogue Coverage

Catalogue coverage: 23 not measured (needs a full run: 1, no agent jobs: 3, no CI file or root pom: 1, no manifests read: 1, no model jobs: 1, no ownership map: 1, no CI check on pull requests: 1, no workflow on pull requests: 1, no remote: 2, no skill files: 2, no skill scripts: 1, no source files: 2, no value read: 2, not a git repository: 1, waits on layer-4-architecture.1: 1, unavailable: 1, the remote's host is not GitHub or a reachable GitLab: 1) · 4 not applicable here (fewer than two agent systems: 2, no configured gate: 2) · model <<normalized>>

By tag: behaviour 0 of 3 (NOT MEASURED: 4) · contracts 0 of 1 · docs 0 of 2 (NOT MEASURED: 2) · structure 0 of 1 (NOT MEASURED: 5) · agent_context 0 of 0 (NOT MEASURED: 4) · agent_runtime 0 of 1 (NOT MEASURED: 4) · governance 0 of 1 (NOT MEASURED: 8) · supply_chain 0 of 0 (NOT MEASURED: 1)

## Where you are on the Steps

In place: Step 2 of 4, Supervised agent PRs: an agent opens pull requests, a person reviews each one.

[![Where you are on the Steps](./step-ladder.svg)](./step-ladder.svg)

Step 3, Unattended agent PRs, opens when CI turns red on a broken check.

## Evidence behind the Step

In place: 0 counted conditions met (0 verified, 0 measured) and 1 listed from config or a declaration.

[![Evidence behind the Step](./evidence-waffle.svg)](./evidence-waffle.svg)

Verified and measured squares count toward catalogue coverage, listed ones are shown but never counted, and hatched ones were not measured, never read as zero.

## Practices in place by truth tag

In place: 6 of the 8 truth tags have a measured condition, each shown with the share of its measured conditions met.

[![Practices in place by truth tag](./tag-profile.svg)](./tag-profile.svg)

Each dot is the share of that tag's measured conditions that are met. A condition with several tags counts on each of its rows and once in the tally, and a dashed row was not measured, never read as zero. No earlier run is recorded here, so no earlier dots are drawn.

## Packages and their imports

In place: this run's scans, gathered with no model call.

Not drawn: not measured here: no source files.

## Docs behind their code

In place: this run's scans, gathered with no model call.

Not drawn: no doc maps to code of its own; 1 doc measured against the whole repository is counted, not drawn.

## Review on recent merges

In place: this run's scans, gathered with no model call.

Not drawn: not measured here: no remote.

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

No structural notes, 0 safe zones.

## Cross-Practice Findings (Keyhole Readiness)

_No cross-practice findings surfaced - no path crossed an axis boundary._

## Changes Since Last Run

_No prior run to compare against - this is the first recorded snapshot._

---

_Deterministic portion of the assessment: headline, metrics, hotspots, and cross-practice findings. The per-practice assessments and prose, and the Top 3 Actions priority narrative require LLM judgement and are written separately._

## How this was measured

In place: over 1 repository, 39 scans were measured everywhere, 21 ran but were not measured everywhere, and 0 were skipped. Every check is described in [docs/what-we-measure.md](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md). A scan that was skipped, or could not read its input, earns nothing and costs nothing.

### Ran (60)

- `agent_ops`
- `config_drift`
- `review_reality`: not measured in 1 of 1 (no_remote)
- `delivery`: not measured in 1 of 1 (no_remote)
- `gate_cost_estimate`: not measured in 1 of 1 (no_remote)
- `coverage_gate`
- `units`
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
- `skill_disclosure`
- `skill_script_tests`
- `skill_personal_data`
- `skill_library`: not measured in 1 of 1 (not_a_git_repository)
- `ci_model_jobs`
- `ci_hazards`
- `ai_governance`
- `org_security_policy`: not measured in 1 of 1 (no_remote)
- `github_secret_scanning`: not measured in 1 of 1 (no_remote)
- `secrets_guard`
- `backlog_ready`
- `agent_consistency`
- `ai_usage`: not measured in 1 of 1 (unavailable)
- `agent_outcome`: not measured in 1 of 1 (not_a_repo)
- `concurrency`: not measured in 1 of 1 (not_a_repo)
- `doc_claims`
- `guardrails`
- `structure`: not measured in 1 of 1 (no_source_files)
- `stats_summary`
- `promissory_markers`: not measured in 1 of 1 (unavailable)
- `structure_drift`: not measured in 1 of 1 (no_ownership_map)
- `observability`
- `doc_graph`
- `doc_staleness`
- `archetype`
- `owner_exclusions`
- `contributor_files`
- `unit_readings`
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

### Skipped (0)

- none
