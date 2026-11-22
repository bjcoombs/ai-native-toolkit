"""Write-side truth pressure: does the test suite actually pin behaviour down?

Coverage says a line *ran*; it never says an assertion would *fail* if that line
were wrong. This package gathers the signals that distinguish a suite which
holds the code to account from one that merely visits it. Two tiers and the
step that merges them, each tier degrading to "not assessed" rather than ever
blocking the assessment:

**Mutation tier (decisive, expensive, opt-in)** - ``mutation.py``. Config
detection plus a bounded, opt-in mutation run and survivor aggregation.

**Cheap-heuristic tier (always-on, candidate signals only)** - ``heuristics.py``.
Three syntactic fingerprints of hollow tests: assertion-on-internal,
untested-boundaries, duplicate-truth.

**Aggregation + facade** - ``aggregate.py``. ``scan_test_pressure`` merges both
tiers into the ``test_pressure`` block consumed by run-context.json.

This ``__init__`` is the stable public facade: it re-exports the same names the
former single-module ``lib.test_pressure`` exposed, so importers and tests need
no change. ``shutil`` and ``subprocess`` are re-exported so existing tests can
monkeypatch ``lib.test_pressure.shutil`` / ``.subprocess`` (the same singleton
module objects the mutation tier calls through).

Boundary: every signal here is a *candidate*. None is a verdict. The output is
grist for human (or LLM) judgement, scoped and labelled as such.

Layer 1 write-side truth pressure. Two tiers:
- Mutation tier: runs a mutation-testing tool (mutmut for Python) over a sample of the
  codebase to measure whether the test suite actually catches changes. mutmut's command
  line differs by major version, so `_mutmut_major` asks the package metadata of the
  interpreter the launcher runs under (shebang, the sh trampoline uv and pip write for
  long paths, or the `python` beside a symlinked launcher) and, when the launcher names
  none, reads what `mutmut --version` reveals in an empty directory. **mutmut 2** runs in
  place and is two-step - `mutmut run` then `mutmut junitxml` - because the run's stdout
  lists only survivors (no totals); the junitxml report carries every mutant, so per-file
  killed/survived/total (and a real survivor density) can be derived, falling back to the
  survivor-only stdout parse when junitxml is empty. **mutmut 3** takes no paths, reads its
  config from `[tool.mutmut]` in `pyproject.toml` or `[mutmut]` in `setup.cfg` of the
  directory it runs in, copies only `source_paths` (plus `also_copy` and a few defaults)
  into `mutants/`, and runs the suite from there. Its adapter, `test_pressure/mutmut3.py`,
  groups the focus files by package root (the nearest directory holding `pyproject.toml`,
  `setup.cfg` or `setup.py`, else the repository root) and runs mutmut once per group,
  from that root, in a fresh scratch copy of the working tree. The copy keeps a tracked
  link only when it points inside the repository (recreated relative); a link out of it
  has a file target copied as content and a directory target left out, so no suite can
  write through the copy into the assessed tree. A package whose config
  mutmut 3 can read runs under it unchanged (its `source_paths`, `also_copy`,
  `pytest_add_cli_args`), after a check that its `source_paths` exist in the copy and
  cover a focus file: a path made at run time (a link CI creates, a generated tree) is
  named in the group's `reason` and not recreated. Focus paths are matched through a
  `source_paths` link (`scripts/lib/x.py` is `src/lib/x.py` under this repo's config), and
  `mutmut run` is given the in-scope files' mutant-name patterns (`lib.x.*`), so the time
  box is spent on the focus files rather than everything the config covers; the config
  and its test selection, and so every verdict, are unchanged. A focus file the config
  does not cover is listed in the group's `unmeasured`. Any other
  package gets a generated `[mutmut]` section: `source_paths` and `paths_to_mutate`
  (3.0-3.5 read only the latter) set to the focus files' top-level entries, `only_mutate`
  (3.6+) to the files, and `also_copy` to the package's other entries, so `mutants/`
  stands in for the package. Because the suite then runs one level deeper than in the
  repository, `_mirror_ancestors` copies each ancestor's entries one level down in the
  scratch copy (the parent's into the package, and so on), so a test that climbs
  `Path(__file__).parents[n]` to a repository file still finds it. The copy is then
  committed to a fresh git repository (user config, hooks and signing kept out), so a test
  that asks git about `HEAD` sees the copied tree. Per-file totals come from
  `mutants/**/*.meta` using the exit-code table of mutmut 3.6.0, mapped back to
  repo-relative paths. Only the focus files are reported whichever config governed a run,
  so the result's `scope` names the files its figures describe. `groups` records each
  package's run (`root`, `config` of `repo` or `generated`, `scope`, `runner`, `mutation_run`, a
  `reason` when it could not run, and `unmeasured` focus files when it ran without them), surfaced as `test_pressure.mutation_groups`; the pass
  counts as run when any group recovered records. Every group draws on one `MUTATION_TIMEOUT` budget: the
  snapshot and the run are bounded by what remains, and a copy (not interruptible) is
  checked against the deadline before and after, so a later group can record a timeout
  without copying. mutmut 3 saves each verdict to its `.meta` as it lands, so a run stopped
  at the budget keeps the mutants it tested: the group reports them with `partial: true`
  and a `reason` saying the figures cover only the mutants tested before the stop (mutmut
  tests the fastest first), and records a timeout only when no focus-file verdict was
  saved. On this path the
  assessed tree is never written to. A version neither probe can
  read takes the mutmut 2 path, which runs in the assessed repo and leaves mutmut's
  `.mutmut-cache` there. Whichever path runs, a tool that exits non-zero without yielding mutants
  puts the last line of its error output (skipping Python warning lines, such as
  mutmut's `paths_to_mutate` deprecation) in the result's `reason` (surfaced as
  `mutation_note`), and the mutmut 3 path appends `; first failure:` with the first
  import error, else pytest's first failing test, scratch and home paths labelled
  (`test_pressure/reason_paths.py`, `cut_prefix`: each spelling of a directory, as given
  and resolved, is cut under `/`, `\\` and a repr's doubled `\\\\` (an `OSError` quotes its
  filename that way), longest first and only before a separator, so a Windows path the
  tool printed with `/` is cut too and the rest of the path reads with `/`; on Windows the
  match ignores case; a root home is never a label, issue #964). Directory entries are mirrored and
  `.meta` files read in code-point name order on every platform (#965). mutmut
  runs pytest under the interpreter that launches it, so `_resolve_runner` picks one that
  can import the suite's dependencies (`runner`): a package `.venv` / `venv` that already
  holds mutmut 3 (read only, no bytecode written), else `uv run --project` with
  `UV_PROJECT_ENVIRONMENT` in the temp dir plus the pinned `mutmut==3.8.0` and pytest (for
  a `pyproject.toml` with a `[project]` table; no optional extras), else the `mutmut` on
  PATH.
  This repo dogfoods the tier: `[tool.mutmut]` in `skills/assess/pyproject.toml` scopes
  mutmut 3 to six core modules (`stats_diff`, `promissory_markers` and its
  `python_regions` helper, `doc_staleness`, `doc_graph` and `keyhole_signals`, per
  `only_mutate`) and `.github/workflows/mutation.yml` runs it weekly,
  scoring per module with `_parse_mutmut3_meta`, so CI and `/assess` count mutants alike.
  That config and workflow are also what `detect_mutation_config` credits on this repo.
  That config mutates through `skills/assess/src`, a committed symlink to `scripts`, so the
  opt-in pass runs it as checked out for the `skills/assess` group, while `scripts/` (no
  mutmut config) runs under a generated section. The walks do not descend the link, and
  the treemap keys files by resolved path, so the linked files are never counted twice. The shared walks
  (`doc_graph`, `structure_graph`, the treemap) skip `mutants/`, so a tree a local run
  leaves behind is never scored.
- Cheap heuristics: test/source ratio, assertion density, and the coverage gap signal -
  fast proxies that run without a mutation tool.
"""
from __future__ import annotations

# Re-exported so tests can monkeypatch lib.test_pressure.shutil/.subprocess.
import shutil
import subprocess

from .aggregate import (
    TestPressureResult,
    _overall_coverage,
    scan_test_pressure,
)
from .common import MAX_FINDINGS, MUTATION_TIMEOUT
from .heuristics import (
    CHEAP_HEURISTIC_NOTE,
    compute_cheap_heuristics,
    detect_assertion_on_internal,
    detect_duplicate_truth,
    detect_untested_boundaries,
)
from .mutation import (
    CLUSTER_MIN_SURVIVORS,
    HIGH_COVERAGE,
    LOW_MUTATION_SCORE,
    MAX_FILES_TO_MUTATE,
    _parse_cargo_mutants,
    _parse_gremlins,
    _parse_mutmut,
    _parse_mutmut_junitxml,
    _parse_stryker_json,
    compute_gap_signal,
    compute_survivor_density,
    detect_mutation_config,
    identify_survivor_clusters,
    run_bounded_mutation,
)

__all__ = [
    "scan_test_pressure",
    "TestPressureResult",
    "detect_mutation_config",
    "run_bounded_mutation",
    "compute_survivor_density",
    "identify_survivor_clusters",
    "compute_gap_signal",
    "compute_cheap_heuristics",
    "detect_assertion_on_internal",
    "detect_untested_boundaries",
    "detect_duplicate_truth",
    "CHEAP_HEURISTIC_NOTE",
    "MUTATION_TIMEOUT",
    "MAX_FILES_TO_MUTATE",
    "MAX_FINDINGS",
    "CLUSTER_MIN_SURVIVORS",
    "HIGH_COVERAGE",
    "LOW_MUTATION_SCORE",
]
