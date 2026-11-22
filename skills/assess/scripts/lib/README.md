# skills/assess/scripts/lib

Deterministic library modules for the `/assess` engine. No LLM calls anywhere in this
package - every function is a pure transform of filesystem, git, or pre-computed signal
data, with two bounded exceptions. Live platform reads are confined to `gh_cli.py`
(GitHub) and `gitlab_cli.py` (GitLab), which run their CLI through `platform_cli.py`;
the headless runner makes them only under `--platform`. They are optional (they need a github.com or
GitLab remote and an authenticated `gh` or `glab`) and degrade to `available: False`
with a reason, never to a clean result. The opt-in mutation pass's uv
runner (`test_pressure/mutmut3.py`) may fetch mutmut, pytest and the assessed package's
dependencies from the package index on first use, into a scratch environment; it runs
only on explicit consent and degrades to a named `reason`. The LLM reads
`run-context.json` after the core finishes; it does not call into these modules.

## The assess_core.py -> lib seam

`assess_core.py` is the orchestrator. It imports from almost every module here, calls
each one in sequence, and assembles the results into `run-context.json`. When the
orchestrator grows a new signal, reorganizes the context schema, or changes what data
blocks it needs, the lib modules that supply that data move in the same commit. This
is cohesion by design: the lib modules exist to serve the orchestrator's data contract,
so their shape is coupled to it by construction.

Two modules are the identified co-change hotspots in the git history:

- **`doc_graph.py`** - the foundation for Layer 0 navigability. Both `doc_staleness.py`
  and the understanding analysis in `keyhole_signals.py` depend on its doc->code
  association edges. When the core adds a navigability signal or changes how it
  represents doc reachability, `doc_graph.py` is the first file that moves.

- **`keyhole_signals.py`** - the integration barrier between the individual signal
  modules and the orchestrator. It assembles all five run-context blocks
  (`behaviour`, `documentation`, `understanding`, `runtime`, `structure`) and emits
  the six named derived findings. Because it touches every upstream signal, it
  co-changes with the core on almost every schema or signal-set change.

The seam is narrowing. Every run-context block is declared once in `scan_registry.py`
(`SCANS`) and run by one loop, so adding a scan edits its own module, its test, that
table and this file - not `assess_core.py`. The setup steps (scope, artifact directory,
run id, stats and diff, instruction grading, first-flagged map) are entries too; their
adapters live in `run_setup.py`, the read-side adapters in `core_scans.py`. The
doc-staleness keys and the doc x complexity join joined the table last (#558), so the
core assigns no key by hand. The table only reads: `run_publish.py` writes the wiki,
badge and `run-context.json` after the scans (#557), so `build_run_context` has no
file-system side effects and `build_and_publish_run` adds the publish step (#660).

Seeing these two files in the same commit as `assess_core.py` is expected, not a
defect.

The core keeps only the orchestration: `build_run_context` (one `run_scans` call,
writing nothing), `build_and_publish_run` (the same, then the publish call, which
`main` uses), `run_opt_in_mutation` and `main`.
Its helpers live in seven modules, one per seam: `instruction_files.py`
(instruction-file grading), `diff_reliability.py` (cross-run diff trust),
`wiki_state.py` (first-flagged dates and run supersession), `run_wiki.py` (the wiki
pages, index and log for one run), `context_blocks.py` (scan results serialised into
run-context blocks), `mutation_cap.py` (the `test_pressure` shape and the Layer 6 cap)
and `run_scope.py` (`--scope` and the artifact directory), plus the
`artifact_schema.py` constant. A change to one of these concerns edits its module and
its test; `assess_core.py` moves only when the order of the pipeline or the
run-context key order changes.

## The wider co-change seams (cohesion, not entanglement)

`/assess`'s own hotspot scan flags several directories as historically co-changing:
`skills/assess/scripts`, `skills/assess/scripts/lib`, `skills/assess/tests`,
`skills/assess/tests/fixtures`, and `scripts` (+ `scripts/tests`). That is the static
import map saying "separate directories" while git says "they move together." Here the
coupling is genuine subsystem cohesion, and naming it is the point - so a reader (or an
agent) trusts the boundary deliberately rather than being surprised by the seam:

- **`scripts/lib` modules <-> `skills/assess/tests`.** Each deterministic module is
  pinned by a test in `skills/assess/tests/`, and the contract in `CLAUDE.md` is explicit
  ("Add a test alongside any change to a deterministic module"). So a module and its test
  are *meant* to move in the same commit - the test is the module's behavioural contract,
  not a separable concern. A reviewer seeing `doc_graph.py` and `test_doc_graph.py` in one
  diff is seeing the intended unit of change.
- **`scripts/` (standalone build) <-> `skills/`.** The standalone-skill build under
  `scripts/` vendors and transforms the very skills it packages, so a change to a skill's
  shape and the build that ships it co-change by construction. That seam is documented in
  `docs/contributing/standalone-skills.md`; it is cohesion between a packager and
  the thing it packages, not a leak.
- **The `actions/checkout` pin across `.github/workflows/*`,
  `skills/assess/templates/assess-gate.yml.template` and `README.md`.** The gate the
  skill generates, the repo's own workflows and the README's copy-paste snippet pin the
  same release by commit SHA. Dependabot bumps only the workflow pins, so the template
  and README follow by hand in the same PR. `skills/assess/tests/test_ci_workflow.py`
  fails the build when the template's pin drifts from `.github/workflows/assess-gate.yml`
  or the README.
- **`fleet_rescore.py` <-> `fleet_data.py`.** `rescore` rebuilds a stored run's cells and
  roll-ups with `fleet_data`'s own `repo_cells` and `row_roll_up`, so a
  change to how `build_fleet_data` computes a cell or a unit roll-up moves both.
  `tests/test_fleet_rescore.py::test_rescore_recomputes_the_unit_roll_ups`
  fails when a re-scored run differs from a fresh `build_fleet_data` over the same rows.

None of these is a refactor task. They are recorded here so the seam is owned: if any is
ever cut, this note is where the intended boundary is written down.

---

## Module reference

There is no per-module index here: each module documents itself in its own module docstring,
a one-line summary followed by the details (`tests/test_self_architecture.py` enforces it).

Windows portability (#712, #752): every text-mode `subprocess.run` names
`encoding="utf-8"` (with `errors="replace"`, or `surrogateescape` on every `git ls-files -z` listing, whose paths must
round-trip), and a repo-relative path is rendered with `.as_posix()`, never `str()`.
`tests/test_windows_portability.py` guards all three across every runtime Python tree: each
`skills/*/scripts/` and the repo-level `scripts/`, leaving out `tests/`, `fixtures/` and dot directories.
The fleet run files (`repos.jsonl`, `groups.jsonl`, `run.json`, `compare.json`, `labels.jsonl`,
`doc_freshness.jsonl`, `agent_clusters.json`, `achievements.json`, `fleet-data.json`, the
generated fleet schemas and the next-steps pages) are written with `newline="\n"`, so Windows and
Linux produce the same bytes; the same test checks that every writer in `lib/fleet*.py` and
`skills/fleet/scripts/` names `newline`. `promissory_markers` asks `rg` for `--path-separator /`
so its paths are POSIX on Windows too, and `change_coupling` keys and sorts co-change pairs
as POSIX strings, so pair order is the same on every OS (#713). That is the canonical order
`structure_drift` already used, and it changed Linux pair order too: `file_a`/`file_b` now
follow POSIX string order, so `src/foo.py` sorts before `src/foo/bar.py` (`.` < `/`), where
the earlier `Path` ordering compared path parts and put `src/foo/bar.py` first. The windows-latest job
(`.github/workflows/windows.yml`) checks both through `tests/test_fleet_golden.py`.
