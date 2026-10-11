# CI and merge gates

The CI reference for `ai-native-toolkit`, moved out of the root `CLAUDE.md` so it is read when needed rather than on every task. `CLAUDE.md` keeps the commands and the list of required checks; this page holds the why. Back to the [Map of Content](../index.md).

## Workflows

- `.github/workflows/pr-lint.yml` validates PR titles against conventional-commit format and auto-applies the matching label (`feat` / `fix` / `docs` / `chore` / `refactor`). Other conventional types (`ci`, `build`, `test`, `perf`, `style`, `revert`) pass validation but get no label.
- `.github/workflows/tests.yml` runs three pytest jobs (`skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`) plus a `ruff + mypy gates` lint job (Layer 3 complexity ratchet + Layer 2 type gate) on every PR and push to `main`. A red test is a real regression: the deterministic core is reproducible, so flakes should not happen.
- `.github/workflows/floor.yml` runs the `floor enforcement` and `floor self-anchor` checks (see [The floor](#the-floor)).
- `.github/workflows/mutation.yml` runs mutation testing weekly (see [Mutation testing](#mutation-testing)).
- `.github/workflows/codeql.yml` (workflow `Security`) runs CodeQL and `pip-audit` (see [Security scanning](#security-scanning)).
- `.github/workflows/build-standalone-skills.yml` publishes the standalone skill ZIPs on a version bump (see [Standalone skill pipeline](./standalone-skills.md)).
- `.github/release.yml` configures categorised release notes for `gh release create --generate-notes`. See the file for the label-to-category mapping.

**CI triggers only on `main`** (`pull_request`/`push` to `main`, plus the weekly `schedule` in `codeql.yml`). A PR targeting `main` gets `tests.yml` + `pr-lint.yml` on every synchronize; pushes to other branches with no open PR run neither.

## Lockfiles

`skills/assess/` and `scripts/` each commit a `uv.lock`, and CI installs from them with `uv run --locked`. The `Lockfiles match pyproject.toml` step in `ruff + mypy gates` runs `uv lock --check` in both packages and fails first on a stale lock, so a `pyproject.toml` edit without `uv lock` reads as a lock problem rather than a lint or type failure. A dependency edit commits the re-locked `uv.lock` in the same PR.

## The type gate

mypy runs `strict` over all of `skills/assess/scripts/` (lib, orchestrators, renderers) with `disallow_any_generics` on for every module and no per-module exception, so a bare `dict` or `list` fails. Known shapes are `TypedDict`s: shared run-context rows and the orchestrators' file shapes, the stats sidecar and `finalize-input.json`, live in `lib/run_context_types.py`; `dict[str, Any]` is for blocks whose keys genuinely vary.

It also covers the `scripts/` build and contract tooling in full, where per-module overrides relax just the flags the floor-protected modules (`floor_anchor.py`, `contract/`, `canaries/`) fail today, and `floor_check.py` stays out.

A new function needs annotations to pass. Both gates enable `ignore-without-code`, so a bare `# type: ignore` fails; the trailing reason (`# type: ignore[code]  # reason`) is a convention mypy cannot check.

## Markdown contract checks

The `plugin contract pytest` job (`tests/test_plugin_contract.py`) runs these over the shipped markdown:

- `test_internal_links_resolve` over every shipped `SKILL.md`: relative markdown links must point to real files. For an illustrative file mention use inline code (`` `CLAUDE.md` ``), not a clickable relative link.
- `test_no_conflict_markers` over every authored markdown file: an unresolved git conflict (a line-starting `<<<<<<<` / `>>>>>>>` / `|||||||`) fails the build. Markdown has no parser to reject one, which is how `commands/tm.md` shipped for about three weeks with three committed conflict regions (#211/#216). Reference a marker as inline code so it never starts a line.
- `test_no_bare_positional_in_skill_md` over every shipped `SKILL.md`: Claude Code substitutes a skill's arguments into a bare `$0`..`$9` anywhere in the text, fenced or not, which is how the assess skill's `needs_offer` helper ran with argument words as its parameters (#328). The placeholders are 0-based (`$0` is the first argument, per [the skills docs](https://code.claude.com/docs/en/skills.md), "String substitutions"), so shell's `$0` (the script name) is corrupted too. Write shell positionals in brace form (`${0}`, `${1}`); for awk, which has no braced field reference, write the parenthesised `$(1)` or restructure the snippet so it takes no positionals. Named `$name` placeholders are not guarded: they exist only for a skill that declares an `arguments` frontmatter list, and none does; a skill that adds one must avoid bare `$<name>` for each listed name.

## File-size ratchet

`tests/test_file_size_ratchet.py` (in `plugin contract pytest`) holds every Python file under `skills/assess/` and `scripts/` to the `default_limit` (800 lines) in `.file-size-ratchet.toml`. Files already over it are listed under `[ceilings]` at their line count and may shrink but not grow.

The ceiling follows the file down: a file more than the larger of `shrink_slack_lines` (20) and `shrink_slack_percent` (2) percent of its ceiling below it fails until the ceiling is lowered to the current count, and an entry must be deleted once its file drops to the limit. A file over its limit gets split; raise a ceiling only for justified growth, with a one-line `reason`.

Because a ceiling can move down, two open PRs can each pass alone and fail together: one shrinks an allowlisted file and lowers its ceiling, the other adds lines to the same file. After the first merges, merge `main` into the second and re-run `plugin contract pytest` before merging it.

## Branch protection

`main` has branch protection (`enforce_admins: true`, 0 required approvals). A PR cannot merge unless seven checks are green: `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, `ruff + mypy gates`, `Validate PR title`, `floor enforcement` and `floor self-anchor`.

Not required: `CodeRabbit`, `Auto-label from PR title`, the conditional `canary suite (semantic layer)`, and the push-only `build` (standalone publish) job. The full non-blocking list, and the `AI-readiness regression gate` that behaves as required, are in [Marathon notes](./marathon.md#ci-patterns).

Verify the live set:

```bash
gh api repos/bjcoombs/ai-native-toolkit/branches/main/protection/required_status_checks | jq .contexts
```

Emergency override: `gh api -X DELETE repos/bjcoombs/ai-native-toolkit/branches/main/protection`, then re-apply.

## The floor

`FLOOR.md` is the constitutional floor of the acceptance-contract workflow: four clauses (cold verification on every run, a contract frozen before decomposition, maintainer sign-off for floor changes, and the immutability of that rule) plus the tokens that mark the files carrying the gate invocations. `.github/workflows/floor.yml` enforces it through the two required floor checks.

A change to the floor core (`FLOOR.md`, `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, per `FLOOR_CORE_PATHS` in `scripts/floor_check.py`) needs the maintainer's out-of-band approval of the `floor-signoff` environment deployment review, which CI requests automatically. No automated step - retro, marathon teammate, or fix loop - may self-apply that approval or route around it: propose the change and stop.

A change to the gate code (`scripts/contract/`) or the canaries (`scripts/canaries/`, `tests/canaries/`) gets no sign-off request; it must pass `canary suite (semantic layer)` instead. The external-anchor proof is [`floor-anchor-proof.md`](../floor-anchor-proof.md).

## Mutation testing

`.github/workflows/mutation.yml` runs `mutmut` 3.8.0 over a bounded set of `/assess` core modules (`stats_diff.py`, `promissory_markers.py` with its `python_regions.py` helper, `doc_staleness.py`, and the two largest hotspots `doc_graph.py` and `keyhole_signals.py`, about 4.5k mutants as of 2026-10; scope in `[tool.mutmut]` in `skills/assess/pyproject.toml`) weekly and on `workflow_dispatch`, never on PRs. This is Layer 6 truth-pressure: evidence that the tests would fail if the code were wrong, which line coverage cannot show.

It is time-boxed (45m inner timeout, 60m job), prints killed/survived/score per module to the job summary, and uploads `mutmut-results` (the survivor listing plus `mutants/**/*.meta`). Surviving mutants are findings to read, not a red build, so the job is never required. To widen the scope, add a module to `only_mutate` and its test file to `pytest_add_cli_args_test_selection`.

mutmut 3 keys mutants by file path with only a leading `src.` stripped, so the run mutates through `skills/assess/src`, a committed relative symlink to `scripts`. Without it every mutant key misses and mutmut stops before testing any, so the workflow fails loudly when the link is missing or retargeted. `/assess`'s walks (treemap, doc graph, structure graph, test pressure), the file-size ratchet, ruff and mypy do not descend the link, so nothing is counted twice (`skills/assess/tests/test_symlink_walks.py`). After a local run, `rm -rf mutants`; `/assess` skips it anyway.

The `/assess` opt-in mutation pass runs this config as checked out, narrowed to the focus files' mutants, inside its own 300s budget; a group stopped at that budget keeps the verdicts mutmut already saved and is marked `partial`.

## Security scanning

`.github/workflows/codeql.yml` (workflow `Security`) runs CodeQL over Python, JavaScript (`scripts/canaries/*.mjs`) and the workflows themselves, plus `pip-audit` over the Python packages CI and the Assess Gate install (the locked dependency sets of both uv projects, `skills/assess/` and `scripts/`, exported with their dev groups from the committed `uv.lock` files, and every script's PEP 723 inline deps, which commit no lock and resolve fresh), on every PR, push to `main` and weekly. Advisory, and the two parts fail differently:

- A `CodeQL (<lang>)` job goes red only when analysis itself fails. Its alerts land in the Security tab and in a separate `CodeQL` code-scanning check, so a green job does not mean no findings.
- `pip-audit` goes red on a known vulnerability or, under `--strict`, on a resolver error, so read the log before acting.

Fix the code rather than dismissing an alert to clear it.

## Coverage floor

There is no coverage target, only a floor that stops coverage falling. The `skills/assess pytest` and `scripts/ pytest` jobs print a term summary and upload a Cobertura `coverage.xml` artifact each (`coverage-skills-assess`, `coverage-scripts`), and fail when line coverage drops below `[tool.coverage.report] fail_under` in that package's `pyproject.toml`: 93 for `skills/assess` (measured 93.44%), 91 for `scripts/` (measured 92.14%).

The floor lives in config rather than as `--cov-fail-under`, so a local pytest-cov run from inside that package directory enforces it too; the repo-root recipe below enforces only the `scripts/` floor, because coverage.py reads `pyproject.toml` from the current directory. It is a ratchet: raise it when coverage rises, and never lower it without a stated reason in the PR that does. Filenames in those artifacts are relative to the job's package directory, not the repo root.

Test coverage focuses on the deterministic core under `skills/assess/`. `complexity-treemap.py` is smoke-tested rather than unit-tested: it is a thin CLI wrapper around lizard/squarify.

### Coverage for `/assess`

`/assess` never runs the suites. It reads an existing `coverage.xml` or `lcov.info` from the repo root (or `coverage/`, `.coverage/`) to fill its "Where to focus testing" table. To give it one, run this from the repo root before `/assess`; it writes a gitignored `coverage.xml` with repo-relative filenames covering both packages. The `scripts/` suite must run from inside `scripts/` (some tests read `../skills/...`), hence the subshell and the combine step:

```bash
GIT_CONFIG_GLOBAL=/dev/null uv run --project skills/assess --with pytest --with pytest-cov --with pyyaml pytest skills/assess -q --cov=skills/assess/scripts --cov-report= \
  && (cd scripts && uv run --with pytest --with pytest-cov pytest -q --cov=. --cov-report=) \
  && uv run --with coverage coverage combine --keep --data-file=.coverage.all .coverage scripts/.coverage \
  && uv run --with coverage coverage xml --data-file=.coverage.all -o coverage.xml
```

## Local runs and git config

`skills/assess/tests/conftest.py` points `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` at the null device at import time, so ambient commit signing or hooks cannot fail the git-backed tests in that suite. Nothing does the same for `scripts/tests/` (`test_floor_check.py` runs `git commit` in temp repos), and scripts run outside pytest (for example `assess_core.py`) still read global config; prefix those with `GIT_CONFIG_GLOBAL=/dev/null` if it signs commits or runs hooks.
