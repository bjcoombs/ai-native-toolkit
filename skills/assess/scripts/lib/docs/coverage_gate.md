# `lib/coverage_gate.py`

The full reference for `skills/assess/scripts/lib/coverage_gate.py`, whose module docstring holds the summary.

Answers the Layer 6 question measurement cannot: is a line-coverage threshold
*configured*? `detect_coverage_gate(repo_root, scope, excludes)` runs from the scan
registry and walks config files (built-in and user excludes via
`assess_config.is_user_excluded`, depth 4 below the walk base, so a monorepo
package's own config counts). A scoped run walks the scope plus the root CI and
task-runner files (`.github/workflows/`, composite actions, CircleCI, root
`Makefile`, `justfile`, `.gitlab-ci.yml`, ...), which run the gates for the scoped
package; a package-level config outside the scope is dropped. A root CI gate is
reported for every scoped run whether or not its command targets the scope, so a
scoped run appends `NOT_DETECTED_SCOPED` to `not_detected` naming that and the
dropped root package configs. It emits the run-context
`coverage_gate` block (`CoverageGateBlock`): `{available, configured, gates,
max_threshold_percent, threshold_meets_minimum, unmeasured_reason, enforced_meets_minimum,
enforced_basis, not_detected}`, each gate
`{file, line, tool, form, threshold, unit, threshold_percent, metric}`. Forms read: coverage.py
`fail_under` in `[tool.coverage.report]` (`pyproject.toml`), `[report]`
(`.coveragerc`) and `[coverage:report]` (`setup.cfg`, `tox.ini`); `--cov-fail-under`
and `coverage report|xml|json|html|lcov --fail-under` in GitHub workflows and
composite actions, CircleCI, `.gitlab-ci.yml`, Jenkins, Azure, Travis, Bitbucket,
`Makefile`, `justfile`, `Taskfile.yml`, `pytest.ini` / `addopts` and the other
task-runner files; Jest `coverageThreshold` (config or
`package.json`); Vitest `coverage.thresholds`; nyc/c8 `check-coverage` (rc file,
`package.json`, or `--check-coverage --lines N`); JaCoCo `<minimum>` in a `pom.xml`
and `minimum =` in a `build.gradle(.kts)` that applies JaCoCo; go-test-coverage's
`.testcoverage.yml` / `.testcoverage.yaml` (#788), one gate per `file:`, `package:` or
`total:` key under `threshold:` (or in an inline `threshold: {total: 80}` map), with
`metric` that key and `line` the line holding the value (read at the repo root on a
scoped run too, like the root CI files); and `cargo tarpaulin
--fail-under N` and `cargo llvm-cov --fail-under-lines N` in the CLI files (metric
`lines`, anchored on the tool so a coverage.py `--fail-under` never reads as Rust, and a
templated value still gates). The threshold is
reported as written (`unit` `percent`, `ratio` for a JaCoCo `0.80`, or
`uncovered_count` for a negative Jest value), never rescaled; a templated value
(`--cov-fail-under=${{ env.COV_MIN }}`, `$COV_MIN`, `$(COV_MIN)`) is a gate with
`threshold: null`. coverage.py and pytest-cov gates carry `metric: total` when the
same config sets `branch = true` in its run section (or the line passes
`--cov-branch`), `lines` otherwise; on a gate in a CI or task-runner file, `lines`
means branch mode was not determined (coverage.py reads the package's own config at
run time, which is not resolved). A templated value may also be Make's escaped
`$$COV_MIN`. Jest, Vitest and nyc/c8 metrics are read inside
the braced object the anchor opens or sits in, so one Jest project's threshold is
never credited to another and a one-line `"c8": {...}` is counted once. Gates are
typed by the `CoverageGate` TypedDict. `configured` (named `enforced` before #783: it
reads configuration text, not a run) is true when
any gate has a non-zero threshold, or a null one from nyc/c8 (which check at a
built-in default) or a templated coverage.py / pytest-cov flag; `fail_under = 0` and a Jest/Vitest anchor whose threshold was
not read are listed but gate nothing.

The threshold reading (#781), which Layer 6 level 1 of the default maturity model
reads. `threshold_percent` is the gate's threshold as a percentage, derived in `_gate`
from `unit` alone by `threshold_percent()`: as written for `percent`, times 100 for
`ratio` (rounded to 6 places, so `0.55` is `55.0`), and null for `uncovered_count` or
a null threshold. `threshold` and `unit` stay as written. `max_threshold_percent` is the
highest non-null `threshold_percent` across all gates, null when none has one.
`threshold_meets_minimum` reads only the gates `configured` counts (`gates_something`),
in this order: true when one has `threshold_percent >= MINIMUM_THRESHOLD_PERCENT` (50);
null (not measured) when none does but one has a null `threshold_percent` (a templated
value, an nyc/c8 default, a Jest uncovered-line count), because it could be 50 or more;
false otherwise, including no counted gate. Measured coverage is never read: a report
is a working-tree file, so the level would stop being a function of the commit.
Go and Rust (#788, #937): when the walk holds a `go.mod` or a `Cargo.toml` (same depth
and excludes; on a scoped run, in the scope or in any directory above it up to the repo
root, as Go and Cargo resolve a module; one under a `testdata/` directory is a fixture
and does not count, `_go_rust_manifests`) and no gate of that language is read
(`_MANIFEST_TOOLS`: go-test-coverage for `go.mod`, cargo-tarpaulin or cargo-llvm-cov for
`Cargo.toml`), a false `threshold_meets_minimum` reads null and `unmeasured_reason` is
`go_rust_gate_form_not_read` (`GO_RUST_UNMEASURED`), because those ecosystems often gate
in a script comparing `go tool cover -func` to a number, which is not read. So a low
Python `fail_under = 10` (or `0`) beside a `go.mod` reads unknown, not unmet (#937): one
language's low gate does not settle another's. A gate of at least 50 percent keeps true,
a null from another gate (a templated threshold) keeps its own reason, and a gate read
for every Go and Rust manifest present, even one gating nothing, keeps the normal
reading, with `unmeasured_reason` null. The collector's manifest `revision` moved to 2 for the
#788 change of meaning, and to 4 in the 3.0.0 release for the #937 widening.
Root coverage.py config on a scoped run (#1032, `lib.coverage_root_config`): coverage.py
reads its config from the directory it runs in, so a root `[tool.coverage]`, `.coveragerc`,
`setup.cfg` or `tox.ini` floor governs a Python scope with no coverage.py config of its own
when CI runs coverage from the root. `_root_floor` scans that root config's `fail_under` and
asks `root_config_applies` where CI's coverage runs run (`working-directory:`, a `cd` in the
same step or GitLab job, an inherited GitLab `before_script` `cd` reading unresolved,
`--rcfile` / `--cov-config`). True: the root gates are counted,
and the root config's `addopts` is read for activation. False (every run resolved, none
reads the root config): dropped. None: the gates are listed in `gates` but left out of
`configured`, `max_threshold_percent` and the CI credit of `enforced_meets_minimum`, and
when they change
`threshold_meets_minimum` it reads null with `unmeasured_reason`
`root_coverage_config_unresolved` (so `enforced_basis` is `threshold_not_read`); a root
floor below 50 leaves false, which is false either way. A scope holding no `.py` file, or
its own coverage.py config, is not affected. Only CI runs on ordinary triggers count. The manifest `revision` moved to 5 for this change of meaning.
Codecov status targets (#882, `lib.coverage_codecov`): the first Codecov config Codecov
reads (`codecov.yml`, `.codecov.yml`, `codecov.yaml`, `.codecov.yaml` at the root, then in
`.github/`, then `dev/`; on a scoped run too) gives one gate per `coverage.status.project`
or `.patch` status, tool `codecov`, `metric` `project` or `patch`, `form`
`coverage.status.<kind>.<name>.target`, unit `percent`, threshold as written (`80%` is 80)
or null for `auto`, no target or a non-number. `informational: true` and `off` / `false`
statuses are not gates; an anchor on a status key (`default: &base`) reads as its block,
and a byte-order mark is skipped. Every status counts (`codecov` is in
`_NULL_THRESHOLD_GATES`, so an `auto` target counts with a null percentage), but a patch
status bounds changed lines only, so `threshold_meets_minimum` reads its percentage of
total coverage as unknown (`coverage_codecov.meets_percent`): it never makes the reading
true, and a patch-only config reads null, never false. When `threshold_meets_minimum` is
null and every counted gate with an unknown percentage is a Codecov one,
`unmeasured_reason` is `codecov_target_not_read` (one is a project status) or
`codecov_patch_only` (all are patch; `coverage_codecov.codecov_reason`). A counted Codecov gate is never run by CI: `coverage_ci.gate_run`
reads it `status_check_unconfirmed` (null), since the status gates a merge only when
branch protection requires it. A config with no `coverage.status` section is not read as a
gate. The manifest `revision` moved to 6.
Adding a gate form: build the gate with `_gate` and the right
`unit`; `threshold_percent` and both block fields follow with no other change. A form
whose number is not a coverage percentage (a count) needs its own `unit`, mapped to
null in `threshold_percent()`, so it reads unknown rather than credited.
Nothing found is `configured: false`, never inferred from a coverage report, and
`not_detected` names the forms not read (Go `-coverprofile` scripts, shell scripts and
other CI systems, Coveralls thresholds and whether a Codecov status is required, Kover, scoverage, SimpleCov, cargo llvm-cov `--fail-under-regions` / `--fail-under-functions`, a `tarpaulin.toml` `fail-under`) so an empty
result reads as "none of these forms". A configured threshold proves the tool
fails below it when coverage runs; `enforced_meets_minimum` (`coverage_ci.py`) adds the run,
and `coverage_activation.py` reads, per config directory, what switches coverage on for a
plain `pytest` or `jest` run (#954). One `--cov-fail-under` gate is read per flag on a line.
JaCoCo `<maximum>` on a `MISSEDCOUNT` or `MISSEDRATIO` counter (and Gradle's `maximum =`
beside `value = "MISSED..."`) is a gate with unit `uncovered_count` or `missed_ratio`:
its `threshold_percent` is null, so it reads unknown, never 0, and any such bound
(zero included) gates something.
Stdlib plus `lib.doc_graph.is_excluded_path`, `lib.assess_config.is_user_excluded`,
`lib.coverage_activation`, `lib.coverage_ci` and `lib.coverage_codecov`. Tests:
`tests/test_coverage_gate.py`, `tests/test_coverage_codecov.py`.
