# `lib/check_command.py`

The full reference for `skills/assess/scripts/lib/check_command.py`, whose module docstring holds the summary.

Pure classifier, split from `agent_environment.py` (issue #698) so an offline scan
can use it without reaching `gh_cli`. `is_check_command(text)` is true for a test
or check target of a runner `command_resolver.RUNNERS` models (`make test`, `npm
run check`, `tox`, the program after `uv run`), or a committed script named for a
check; an install (`npm ci`, `uv sync --extra test`) never is. `is_check_name(name)`
splits a target name on `:_./-` and looks for a word of `CHECK_NAMES` (`CHECK_WORDS`, no `ci`:
#951, so CI's `make ci` reads null; `lib.named_check` adds `ci` for instruction files).
For `make`, `mvn`, `gradle`, `go` and `cargo` the first positional names the work, skipping a
shell expansion (`mvn ${MVN_ARGS} verify` runs `verify`; #799).
`is_lint_command(text)` walks the same program/target anchoring with `LINT_WORDS`
(`lint`, `vet`, `clippy` and the linters) and `LINT_PROGRAMS` (`ruff`, `eslint`,
`mypy` ... run directly), so `make lint` and `npx eslint` count and an install
naming a linter does not; instruction-file classification does not use it. Used by
`agent_environment` (instruction-file commands, entry points) and `ci_checks` (CI
lines). `is_lint_run(text)` (issue #782) is the lint test `ci_checks.lint_runs`
reads: `is_lint_command` without a `--version` / `--help` / `-V` call (`ruff
--version` runs no lint) and without the package-manager test aliases
`is_lint_command` credits (`npm test`, `npx jest`), plus a go-task lint target
(`task lint`), `hatch fmt --check` and `hatch run` of a linter program (`hatch run
ruff check .`). `is_info_invocation(text)` (issue #919) is that version and usage test
on its own: `--version` or `--help` anywhere, `rspec --init`, and flags read only for the
program they belong to: `-V` when the program is a linter, type checker, `pytest`, `tox` or
`nox` (elsewhere `-V` can run the work: `mvn -B -V verify`, `./gradlew -V test`); `-h` for those and the known runners (`_H_RUNNERS`: dart, flutter, dotnet, go,
cargo, ctest, rspec, make, just, bazel, jest, vitest, mocha, swift, deno, phpunit), never a
script (`./scripts/test.sh -h db` names a host); `--collect-only` / `--co` for pytest,
`--list-tests` for dotnet, phpunit and swift, `-N` / `--show-only` for ctest. The program is
read through every wrapper `check_command` knows (`_info_program`: `uv run`, `npx`, `python
-m`, the #920 wrappers and `hatch run`, so `bundle exec rspec -h`, `docker compose run app
pytest --co` and `hatch run pytest -V` are info calls, #936), and `ci_checks` drops such a
command from every CI line test, `may_check` included, so
`pytest --version` is no Layer 5 check and a version-only step makes
`test_or_lint_configured` false. Type checkers count as linters there, in a vocabulary local to
`is_lint_run` (`pyright`, `basedpyright`, `tsc`, `vue-tsc`, and `typecheck` /
`type-check` targets such as `npm run typecheck` or `make type-check`), so
`is_lint_command` and Layer 5 are unchanged. A package-manager runner form (`npx`, `bunx`, `bun x`,
`npm exec`, `pnpm exec` / `dlx`, `yarn exec` / `dlx`) is read by the program it runs:
a linter or type checker is a lint run, a test runner (`jest`, `vitest`, `playwright
test`) is neither, and any other program is `may_lint` (null, never false). A lone
type-checker item (`- tsc` under `needs:`) is a value like a lone linter. `may_lint(text)` is a check that may run a linter the line cannot
show: a check target of a runner or script (`make check`, `npm test`, `tox`,
`./scripts/test.sh`, `task check`), `pre-commit run`, or a `hatch run` script
(`hatch run lint`, `hatch run test:cov`; like `tox -e`, its recipe is not read), never a direct test-program
call (`pytest`, `uv run pytest`, `python -m pytest`, `go test`, `cargo test`, `npx
jest`). `is_lint_command` keeps its meaning.
Test runners and wrappers beyond `RUNNERS` (issue #920): `check_verdict(text)` is
`is_check_command` as three states. True for a test program (`TEST_PROGRAMS`: `ctest`,
`phpunit`, `pest`, `rspec` and the JS test runners, matched by basename so
`vendor/bin/phpunit` counts), the `test` subcommand of a toolchain (`TEST_SUBCOMMANDS`:
`dotnet`, `swift`, `mix`, `deno`, `bazel`, `bazelisk`, `hatch`, `flutter`, `rails`, `dart`), `php artisan test`, a
Gradle test task (`./gradlew testDebugUnitTest`, `connectedAndroidTest`), the
`test` action of `xcodebuild` or a check-named target of
a target runner (`TARGET_RUNNERS`: `make`, `just`, `task`, `rake`, `turbo`, `nx`, with
`nx affected -t test` and `nx run app:test` read). A wrapper is read by the program it
runs (`docker compose run|exec <service>`, `bundle exec`, `uv run`, `poetry run`,
`npx`, `pnpm exec`, `hatch run`), in `is_lint_command`, `is_lint_run` and `may_lint`
too. None (`may_check(text)`) for a target whose recipe is not read: any other target
of a target runner, its default target (a bare `make`), a `hatch run` script that is not
check-named, a package script (`npm run foo`, `pnpm foo`, `yarn foo`), a committed script
(`./scripts/foo.sh`, `bash ci/foo.sh`, `bin/foo`; only a `./` or `bin/` head, a `.sh`
file or a path passed to `bash`/`sh`/`dash`/`zsh`/`source` (`.` too, #1033), read by file name so `./ci/deploy.sh` is
false), and a container's default command or an unknown
program behind any wrapper (`docker compose run app`, `docker run img some-tool`, `uv run foo`,
`poetry run foo`, `pipx run foo`, `uvx foo`, `npx foo`, `pnpm exec foo`, `yarn dlx foo`).
Names whose first word is in `SETUP_TARGETS` (`install`, `deps`, `build`, `deploy`,
`format`, `release`, `docs`, `configure`, ...) stay false, as do installs, `bash -c`,
absolute paths and a container running a plain program (`echo`) or a modelled runner.
A target runner asked for `--version` or its list (`task --list`, `rake -T`) is false.
`may_lint` reads the direct runners (`dotnet test`, `mix test`, `hatch test`, `ctest`,
`bundle exec rspec`) as tests only; `bazel test`, `turbo`, `nx` and `task` targets may
lint. `BARE_RUN_PROGRAMS` is the set a lone bare YAML item is a value for
(`ci_lines._lone_check`). Other ecosystems (issue #926): `LINT_PROGRAMS` takes the
analysers of `lint_vocabulary.ANALYSER_PROGRAMS`, and `is_lint_command` and `is_lint_run`
also accept `lint_vocabulary.ecosystem_lint` (a formatter in its check mode, `mix credo`,
`dart analyze`, a Gradle lint task). `_plain_wrapper` reads `xargs [flags] <program>` and
`dotnet tool run <program>`, and (#799) `xvfb-run [flags] <program>` and `timeout [flags]
DURATION <program>`, as the program they run, in `_wrapped` too, where a non-check
program behind them stays false (`xargs rm` is not a target whose recipe is unread).
`is_format_rewrite(text)` is a formatter that rewrites files (`clang-format -i`,
`xargs clang-format-21 -i`, `mix format`), read by `ci_lint_gates`. `is_info_invocation`
reads the program by `lint_vocabulary.program_name` (`clang-tidy-18 -h`). `nx graph`,
`nx show`, `nx list`, `nx report`, `nx reset` and `nx daemon` print information:
`check_verdict` reads them false, not null, and so do `npx nx graph`, `pnpm exec nx graph`
(`may_lint` too) and `pnpm nx graph` / `yarn nx graph` (a package-manager head running a
target runner is read as that runner). Tests: `tests/test_check_command.py`,
`tests/test_check_runners.py` (#920), `tests/test_lint_vocabulary.py` (#926),
`tests/test_agent_environment.py` (`test_is_check_command`), `tests/test_ci_checks.py`
(`test_lint_runs_per_command`).

Shell, make and hatch parsing (issue #955, `lib.command_flags`). A shell's own flags are
read per program: `-e` takes no value, so `sh -e ./ci/run.sh` and `bash -e scripts/test.sh`
run their script; `-o` / `+o` / `-O`, a cluster ending in `o` (`-eo pipefail`),
`--rcfile` and `--init-file` take one; only a single-dash cluster holding `c` is `-c`, so
`bash --norc ./ci.sh` runs `ci.sh`. `bash -c "<cmd>"` is read by `_outer_wrapper` as the
command its string runs when that is one plain command (`bash -c "make test"` is `make
test`); a list, pipeline, redirection or substitution in the string, or a string that cannot
be split, reads null (`check_target`), because the masking of `lib.ci_masking` cannot see
inside the quotes. make's option values are dropped before its targets are read
(`make -j 4 build` is `build`, false). `hatch run <name>` has the name trust of `make
<name>`: `hatch run lint` is a lint command and a lint run, as `make lint` is.
`pdm [run]` and `poe` are read the same way (#1046, `lib.runner_args.script_run`): `pdm run
ruff check .` is a lint run, `pdm run lint` and `poe lint` have the name trust of `make
lint`, and any other script or task reads null (`may_check`, `may_lint`), wrapped or not
(`uv run poe lint` is `poe lint`). A pdm script named in `[tool.pdm.scripts]` of the root
`pyproject.toml` shadows the program (`pdm run pytest` with a `pytest` script runs the
script), and a script or poe task named like a program this module reads (`poe ruff`) is
`runner_args.SCRIPT`, a script whose recipe is unread: null, never credit. A bare `pdm
<word>` that is neither a pdm command nor a script runs nothing (pdm exits 2). The scripts
are read only inside `ci_checks.scan_ci_checks` and `coverage_gate.detect_coverage_gate`;
elsewhere a bare `pdm <word>` is read as a script. Gradle (#1046): a dry run (`-m`, in a
cluster such as `-qm`, `--dry-run`, `--task-graph`) runs nothing, like `make -n`
(`runner_args.runs_no_task`), and a task named after `-x` / `--exclude-task` is not run
(`runner_args.gradle_run`: `check -x check` and `ktlintCheck -x ktlintCheck` run no task).
Gradle (#1124): every task named counts (`clean check`), read by `runner_args.gradle_tasks`
so an option's value is no task (`--warning-mode all check`); `build` reads true where a
JVM or Android plugin is applied in a tracked build file, null otherwise
(`runner_args.gradle_build_verdict`), false with `-x check` or in a dry run.
`_js_lint` reads the package managers only; `is_lint_run` reads `npx` / `bunx` by the
program they run before it.
