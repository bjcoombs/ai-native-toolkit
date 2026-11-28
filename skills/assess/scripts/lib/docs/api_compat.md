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
| `apidiff` | `apidiff ...`, or `go run <path>/apidiff[@v]`, in a job, script or recipe that re-raises (below) |
| `api-extractor` | `api-extractor run` without `--local` / `-l` (which rewrites the report instead of stopping) |
| `griffe` | `griffe check` |
| `cargo-semver-checks` | `cargo semver-checks`, `cargo-semver-checks`, a step that `uses: obi1kenobi/cargo-semver-checks-action` |
| `buf-breaking` | `buf breaking`, a step that `uses: bufbuild/buf-breaking-action` |
| `japicmp` | `japicmp` or `java -jar <...japicmp...>.jar` with an `--error-on-*` flag; a Maven `japicmp-maven-plugin` goal or a Gradle `japicmp` plugin the asked task reaches, with a stop setting (below) |
| `revapi` | `revapi`, a Maven `revapi-maven-plugin` goal or a Gradle `revapi` plugin the asked task reaches |

**A run counts only when it can turn the job red.** Two tools report without stopping by default:

- `apidiff` (`golang.org/x/exp/cmd/apidiff`) exits 0 on an incompatible change; it only prints the report. It counts when the job's lines, or the followed script or recipe that runs it, hold a command that re-raises: `exit` or `return` with a non-zero literal or a variable, or `false` (`if [ -s out.txt ]; then exit 1; fi`). A whole-line capture (`out=$(apidiff a b)`) is read as the command it runs.
- `japicmp` exits 0 unless the CLI gets an `--error-on-*` flag. The Maven plugin's `breakBuildOn*` and `breakBuildBasedOnSemanticVersioning`, and the Gradle task's `failOnModification`, `failOnSourceIncompatibility` and `failOnBinaryIncompatibility`, default to false: one of them set true in a build file the follower read, or a `-Djapicmp.<setting>=true` on the command, is needed.

A run that cannot stop its job is listed under `declared` with the CI file as its path, never counted.

The command is read from a CI shell body, or from what the shared CI-step follower (`lib.ci.ci_step_follower`) returns for it: a committed script, a `make` recipe, local composite actions and reusable workflows, and the Maven and Gradle plugin bindings (`lib.ci.ci_build_follower`). Up to three levels are followed. A root `package.json` script is followed for `npm run <t>`, `pnpm run <t>`, `yarn <t>` and `bun run <t>`.

Not an invocation: an install (`go install`, `npm i -D`, `cargo install`, `pip install`), `--help` and `--version` (`lib.ci.check_command.is_info_invocation`), an `echo`, and the masked forms below. A Maven run whose follower `unknown` names the tool (`-Djapicmp.skip=true`) is not credit and leaves the reading unsettled.

## Pinning-test arm

- Python: a file named `test_*.py` or `*_test.py`, parsed with `ast` (never imported), holding an `==` comparison with `__all__` (a name or an attribute) on exactly one side and, on the other, a list, tuple or set literal of names or a name assigned one in the file. `assert set(__all__) == set(EXPECTED_ALL)` pins. `len(__all__) > 0`, `a.__all__ == b.__all__` and a comparison with `dir(mod)` or `inspect.getmembers(mod)` do not: those check `__all__` against the module, so a removed name passes when `__all__` drops it too.
- JavaScript and TypeScript: a `*.test.*` or `*.spec.*` file with `expect(Object.keys(<module>))` compared by `toMatchSnapshot`, `toMatchInlineSnapshot`, `toEqual` or `toStrictEqual`; a `//` comment line is skipped.
- Files under `fixtures/`, `testdata/`, `vendor/`, `node_modules/` or `third_party/` are set aside.

The arm counts when CI runs tests: `ci_checks.enforced_check_runs` is true, the reading the layer-5 condition uses. The collector reads the `ci_checks` block for that (`reads = ["repo_root", "ci_checks"]`).

## Declared only

A CI run that cannot stop its job (above) is declared. So is a tool token named in a build file (`pom.xml`, Gradle build and settings files, `package.json`, Makefiles, `justfile`, `pyproject.toml`, `Cargo.toml`, `buf.yaml` (a `breaking:` section counts), `api-extractor.json`, `noxfile.py`, `tox.ini`) or in a file under `hack/`, `scripts/`, `build/` or `.github/` (a workflow is read as CI, never as a declaration). A declared tool that a CI run reaches is not listed again. Docs (`.md`, `.rst`, `.txt`, `.adoc`, `.html`) never declare. Declared tools are listed and never counted.

## Masking

- CI files are read through `lib.ci.ci_masking.mask_lines`: `|| true`, `continue-on-error: true`, `allow_failure: true` and a never-true `if:` do not count. A tool on a line `mask_lines` returns as unsure (a `||` fallback that can pass, `pytest | tee` with pipefail off) is unsettled.
- A followed script or recipe line whose `||` fallback does not re-raise does not count and is not followed (`lib.ci.docs_kept_true._commands`).
- Only shell bodies count: GitHub `run:`, GitLab `script:` and `before_script:`. A GitLab `after_script:` failure does not change the job status, so it is not read.
- GitLab jobs: a job whose own `when:` is `never` or `manual`, or whose every `rules:` entry is, runs nothing (`lib.ci.agent_evals._idle`). A hidden job (`.compat:`) runs only through `extends:` from a job that runs, followed to a fixed point. A tool in a hidden job nothing extends counts nothing; in a file that uses YAML aliases, merge keys or `!reference`, it is unsettled.

## Not measured

`present` is null with:

- `tests_not_read`: no tool runs, a pinning test is tracked, and `ci_checks.enforced_check_runs` is null or the `ci_checks` block is absent or degraded.
- `ci_not_read`: no tool runs and no pinning test counts, a tool is declared or found in an unsettled form, and a tracked CI file of a system this reader does not parse (`lib.unread_ci`), a `.gitlab-ci.yml` top-level `include:`, or a follower reading with an unsettled part (a remote reusable workflow, an unread Gradle `apply from:`) may run it.

With no tool and no pinning test anywhere in the tree, `present` is false even beside an unread CI system: nothing in the repository names a check that system could run.

## Known not detected

These are fixtures in `tests/test_api_compat_condition.py`, asserted unmet:

- An API-surface ratchet test: a committed surface file a test compares, regenerated behind a flag (the camel shape).
- A migration check (`makemigrations --check`, the django shape). The missing-migration arm was dropped (maintainer decision, 2026-11-25).

## Known limits

- GitLab `include:` files are not followed; a hit in one is not read (a declared tool then reads `ci_not_read`).
- A reusable workflow under `.github/workflows/` that only `workflow_call` triggers is read as its own CI file, so a tool in it counts whether or not a caller runs it.
- `set +e` inside a followed script is not read.
- A pinning test counts when CI runs any tests, not only the folder that holds it.
- Repository-specific compatibility checks (the vscode `vscode-dts` compile, the grafana API-change script when it runs no named tool) are not detected.
