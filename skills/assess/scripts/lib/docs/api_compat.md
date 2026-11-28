# `lib/ci/api_compat.py`: API compatibility checks CI runs

Issue #853, theme 8 of #826. The module docstring is the summary; this page holds the detail and the known limits. Paths are relative to `skills/assess/`.

## Block

`api_compat` in `run-context.json` (shape `lib.run_context_types_api_compat.ApiCompatBlock`):

| Field | Meaning |
|---|---|
| `files_read` | CI files read (GitHub Actions workflows directly under `.github/workflows/`, `.gitlab-ci.yml`) |
| `tools_run` | distinct compatibility tools a CI run reaches |
| `pinning_tests` | tracked test files that pin the public exports |
| `declared_only` | distinct tools named in build files that no CI run reaches |
| `present` | the `api-compat.1` condition: true, false or null |
| `present_reason` | `tests_not_read` or `ci_not_read`, only when `present` is null |
| `runs` | `{path, tool}`: the CI file and the tool it runs |
| `pins` | the pinning test paths |
| `declared` | `{path, tool}`: the build file and the tool it names |

Counts-only keeps every field: paths as paths, tool tokens and the reason as tokens (`checks/core/api_compat.toml`).

## Named-tool arm

A command invokes a tool when, past a launcher (`uv run`, `npx --yes`, `pnpm exec`, `python -m`), its program is:

| Token | Command |
|---|---|
| `apidiff` | `apidiff ...`, or `go run <path>/apidiff[@v]`, in a job, script or recipe that re-raises (below); a step that `uses: joelanford/go-apidiff` (it exits with go-apidiff's code, non-zero on an incompatible change) |
| `api-extractor` | `api-extractor run` without `--local` / `-l` (which rewrites the report instead of stopping) |
| `griffe` | `griffe check` |
| `cargo-semver-checks` | `cargo semver-checks`, `cargo-semver-checks`, a step that `uses: obi1kenobi/cargo-semver-checks-action` |
| `buf-breaking` | `buf breaking`, a step that `uses: bufbuild/buf-breaking-action` |
| `japicmp` | `japicmp` or `java -jar <...japicmp...>.jar` with an `--error-on-*` flag; a Maven `japicmp-maven-plugin` goal or a Gradle `japicmp` plugin the asked task reaches, with a stop setting (below) |
| `revapi` | `revapi`, the Maven `revapi-maven-plugin:check` goal or a Gradle `revapi` plugin the asked task reaches, unless `failBuildOnProblemsFound` is set false (in a build file read or as `-Drevapi.failBuildOnProblemsFound=false`); a `report`-only goal never stops the build |

**A run counts only when it can turn the job red.** Two tools report without stopping by default:

- `apidiff` (`golang.org/x/exp/cmd/apidiff`) exits 0 on an incompatible change; it only prints the report. It counts when its output decides a re-raise later in the same shell: a later command on its own line that reads its pipe (`apidiff a b | grep -q . && exit 1`; in a GitHub step with pipefail off the #1061 masking drops the pipe's left side, so the line is read as written, #1617: apidiff exits 0 anyway and the pipe's last command is the check, as in a followed script; a line with a background `&` is not read this way, since its `exit 1` runs in a background job), or a later command of its own line (`apidiff a b > out.txt; test ! -s out.txt || exit 1`, the re-raise after the command that reads the output) or a later line that names, as a whole word, its captured variable (`out=$(apidiff a b)`, then `$out`) or the file apidiff's own command is redirected or tee'd to (never `/dev/*`; in `apidiff a b; echo x > other.txt`, `other.txt` is echo's), and re-raises. The command that reads the output must decide the re-raise (`_decides`): the last command of its pipeline succeeds on a non-empty report (`[ -s f ]`, `[ -n "$out" ]`, `[ $(wc -l < f) -gt 0 ]` or any `-gt N`, `grep` without `-v`; a leading `if` is dropped and each `!` turns it over, so `if ! [ -z "$(cat f)" ]` reads the same as `-n`) and its `&&` chain holds the re-raise (`[ -s f ] && cat f && exit 1`; in `[ -s f ] && echo x || exit 1` the `exit 1` runs only when the test fails), or fails on one (`[ ! -s f ]`, `[ -z "$out" ]`, `diff`, `cmp`) and `||` does, or it is an `if` test that succeeds on one with a re-raise in its `then` branch. So `cat out.txt; go build || exit 1` (go build's fallback) and `test -s out.txt || exit 1` (red only when nothing changed) do not count. A test of an inline substitution counts too: `if [ -n "$(apidiff a b)" ]; then exit 1; fi` (`then` after `;` or on the next line, the branch read on to `fi`, across lines), `test -n "$(apidiff a b)" && exit 1`, `[ -z "$(apidiff a b)" ] || exit 1`, with `[`, `[[` or `test`; its `&&` chain or `||` fallback is read as for an output file (`[ -z "$(apidiff a b)" ] || { echo broke; exit 1; }`, `test -n "$(apidiff a b)" && echo broke && exit 1`). In a CI step the masking blanks an `if` test alone on its line (a condition cannot fail the step); the test is kept, as written, when the next line of its body survives the masking, so a masked step (`continue-on-error`) still reads nothing. A `fi`, `elif` or `else` line the masking blanked is kept the same way, when another line of its step survives the masking, so a `fi || true` still rescues the block and an `elif` branch's re-raise is never read as the `then` branch's. `-z` with `then exit 1`, or `-n` with `|| exit 1`, goes red only when nothing changed, so it does not count; such a run, and any other substitution of apidiff that decides no re-raise, is declared. An `exit 1` that reads nothing apidiff wrote is not its check. The re-raise is the command itself, never an argument (`echo false`, `echo exit 1`): `exit` or `return` with a non-zero literal or a variable other than `$?` or one assigned `$?` (`rc=$?; exit $rc`), or `false` (`if [ -s out.txt ]; then exit 1; fi`). The shell is the GitHub `run:` step (another step is another shell), the GitLab job's `before_script:` and `script:`, or the followed script or recipe. apidiff's own `||` fallback never runs, so `apidiff a b || exit 1` and `apidiff a b || { echo broke; exit 1; }` do not count, nor does an `exit 1` before the call. A whole-line capture (`out=$(apidiff a b)`) is read as the command it runs.
- `japicmp` exits 0 unless the CLI gets an `--error-on-*` flag. The Maven plugin's `breakBuildOn*` and `breakBuildBasedOnSemanticVersioning`, and the Gradle task's `failOnModification`, `failOnSourceIncompatibility` and `failOnBinaryIncompatibility` (Groovy `= true`, Kotlin `.set(true)`), default to false: one of them set true in a build file the follower read, or a `-Djapicmp.<setting>=true` on the command, is needed.

A run that cannot stop its job is listed under `declared` with the CI file as its path, never counted.

The command is read from a CI shell body, or from what the shared CI-step follower (`lib.ci.ci_step_follower`) returns for it: a committed script, a `make` recipe, local composite actions and reusable workflows, and the Maven and Gradle plugin bindings (`lib.ci.ci_build_follower`). Up to three levels are followed. A root `package.json` script is followed for `npm run <t>` (and `npm run-script`), `npm test` with its built-in aliases `npm t` and `npm tst`, `pnpm <t>`, `yarn <t>`, `bun run <t>` and `bun <t>` when `<t>` is not one of bun's own commands (`bun test` runs bun's test runner, never the `test` script). The package manager is matched by name, so `/usr/local/bin/yarn test` is followed too.

Not an invocation: an install (`go install`, `npm i -D`, `cargo install`, `pip install`), `--help` and `--version` (`lib.ci.check_command.is_info_invocation`), an `echo`, and the masked forms below. A Maven run whose follower `unknown` names the tool (`-Djapicmp.skip=true`) is not credit and leaves the reading unsettled.

## Pinning-test arm

- Python: a file named `test_*.py` or `*_test.py`, parsed with `ast` (never imported), holding an `==` comparison with `__all__` (a name or an attribute) on exactly one side and, on the other, a list, tuple or set literal of names or a name assigned one in the file, optionally wrapped in `set`, `sorted`, `list`, `tuple` or `frozenset`, and nothing more (`set(dir(m)) - {"helper"}` holds a literal but is not one). `assert set(__all__) == set(EXPECTED_ALL)` pins. `len(__all__) > 0`, `a.__all__ == b.__all__` and a comparison with `dir(mod)` or `inspect.getmembers(mod)` do not: those check `__all__` against the module, so a removed name passes when `__all__` drops it too.
- JavaScript and TypeScript: a `*.test.*` or `*.spec.*` file with `expect(Object.keys(<module>))` compared by `toMatchSnapshot` or `toMatchInlineSnapshot`, or by `toEqual` or `toStrictEqual` with a literal array or object (never `.toEqual(Object.keys(other))`, which compares two live surfaces); a `//` comment line is skipped.
- Files under `fixtures/`, `testdata/`, `vendor/`, `node_modules/` or `third_party/` are set aside.

The arm counts when CI runs tests: `ci_checks.enforced_check_runs` is true, the reading the layer-5 condition uses. The collector reads the `ci_checks` block for that (`reads = ["repo_root", "ci_checks"]`).

## Declared only

A CI run that cannot stop its job (above) is declared. So is a tool token named in a build file (`pom.xml`, Gradle build and settings files, `package.json`, Makefiles, `justfile`, `pyproject.toml`, `Cargo.toml`, `buf.yaml` (a `breaking:` section counts), `api-extractor.json`, `noxfile.py`, `tox.ini`) or in a file under `hack/`, `scripts/`, `build/` or `.github/` (a workflow is read as CI, never as a declaration). griffe is declared only by `griffe check` or a `[tool.griffe]` table: `griffe` is also the engine mkdocstrings depends on, and its `griffe-*` extensions are docs plugins, so a griffe dependency declares nothing. A declared tool that a CI run reaches is not listed again. Docs (`.md`, `.rst`, `.txt`, `.adoc`, `.html`) never declare. Declared tools are listed and never counted.

## Masking

- CI files are read through `lib.ci.ci_masking.mask_lines`: `|| true`, `continue-on-error: true`, `allow_failure: true` and a never-true `if:` do not count. A tool on a line `mask_lines` returns as unsure (a `||` fallback that can pass, `pytest | tee` with pipefail off) is unsettled.
- A followed script or recipe line whose `||` fallback does not re-raise does not count and is not followed (`lib.ci.ci_job_commands.commands`).
- An apidiff output check on a line that a passing `||` fallback follows (`if [ -s o ]; then false; fi || true`) does not count, in a CI step too: the line is read as written, because the CI masking drops the `if` test and with it the `fi || true`. A `fi || true` on a later line that closes the tested `if` (nested blocks counted), or a passing `||` later on the line after a `&&` chain or `||` fallback (`grep -q . out.txt && { echo broke; false; } || true`), rescues `false` or any other failing command, so the check does not count. An `exit N` (N a non-zero literal) is not rescued, because it ends the shell before the `|| true` runs, so `grep -q . out.txt && { echo broke; exit 1; } || true` and `if [ -s out.txt ]` / `then` / `exit 1` / `fi || true` count, as they did before #1617. The `exit N` must be reached whenever the branch runs: on a line of the `then` branch where it runs whenever the line does (`exit 1`, `then exit 1`, `echo broke; exit 1`, `echo broke && exit 1`; never behind `||`, a test or a subshell on that line), in a branch that holds no nested block (any nested `if`, `case`, `while`, `for` or `until` in the branch removes the credit, even for an `exit 1` outside it), and not in an `else` branch or a subshell (`( exit 1 )` ends only the subshell; a subshell is a command that opens with `(`, so a quoted `(` in a message such as `echo "API break (see above)"` is not one); in the `&&` chain itself after only `echo` or `printf` commands (any other command may fail and skip it: `{ false && exit 1; } || true` is rescued), as the whole `||` fallback, or after a `;` inside the `{ }` group that opens the branch, with no other brace in the group up to it (a quoted brace is not read as shell, so such a group is not credited). Anything else falls back to the rule that a later passing `||` rescues.
- Only shell bodies count: GitHub `run:`, GitLab `script:` and `before_script:`. A GitLab `after_script:` failure does not change the job status, so it is not read.
- GitLab is read as GitLab merges it (`lib.ci.ci_merged_view`, #1180): each source with its merged jobs blanked, then the rendered merged jobs, so local includes are read and an `extends:` job runs its effective `script:` (GitLab replaces an array, so a job that sets its own `script:` never runs the template's). Without a readable merged configuration the root file alone is read, with the same rule applied to `extends:`.
- GitLab jobs: a job whose own `when:` is `never` or `manual`, or whose every `rules:` entry is, runs nothing (`lib.ci.agent_evals._idle`). A hidden job (`.compat:`) runs only through `extends:` from a job that runs and does not set that body itself, followed to a fixed point. A tool in a hidden job nothing extends counts nothing; in a file that uses a YAML alias where YAML reads one (a whole value, `key: *name`, `"key": !!seq *name`, `- *name`, or a flow-sequence item, `key: [*name, make]`), a merge key or `!reference`, it is unsettled. A shell glob (`ls *.txt`, `script: [ls *.txt]`) is not an alias.

## Not measured

`present` is null with:

- `tests_not_read`: no tool runs, a pinning test is tracked, and `ci_checks.enforced_check_runs` is null or the `ci_checks` block is absent or degraded.
- `ci_not_read`: no tool runs and no pinning test counts, a tool is declared or found in an unsettled form, and a tracked CI file of a system this reader does not parse (`lib.unread_ci`), a `.gitlab-ci.yml` top-level `include:`, or a follower reading with an unsettled part (a remote reusable workflow, an unread Gradle `apply from:`) may run it.

With no tool and no pinning test anywhere in the tree, `present` is false even beside an unread CI system: nothing in the repository names a check that system could run.

## Known not detected

These are fixtures in `tests/test_api_compat_condition.py`, asserted unmet:

- An API-surface ratchet test: a committed surface file a test compares, regenerated behind a flag (the camel shape).
- A migration check (`makemigrations --check`, the django shape). The missing-migration arm was dropped (maintainer decision, 2026-11-25).

The re-raise rules (`reraises`, `branch_reraises`, `line_ends`) live in `lib.ci.apidiff_reraise`, split out to keep `api_compat.py` under the file-size ratchet.

## Known limits

- A GitLab include the merged configuration cannot read, and a rendered job it cannot settle, are left out; a declared tool then reads `ci_not_read`.
- A reusable workflow under `.github/workflows/` that only `workflow_call` triggers is read as its own CI file, so a tool in it counts whether or not a caller runs it.
- `set +e` inside a followed script is not read.
- A GitLab anchor on a key's value (`script: &cmds` with the list below it) is not read as that key's body, so a tool in it is not seen; an anchor on the job itself (`.compat: &compat`) is.
- A re-raise in an `else` branch (`if [ -z "$(apidiff a b)" ]; then echo ok; else exit 1; fi`) is not read as a check; the run is declared.
- An apidiff output test other than `-n` or `-z` on one `$(...)` (`[ "$(apidiff a b)" != "" ]`), and a read of its output file other than `-s`, `-n`, `-z`, a count compared with a literal, `grep`, `diff` or `cmp`, is not read as a check; the run is declared.
- A pinning test counts when CI runs any tests, not only the folder that holds it.
- Repository-specific compatibility checks (the vscode `vscode-dts` compile, the grafana API-change script when it runs no named tool) are not detected.
