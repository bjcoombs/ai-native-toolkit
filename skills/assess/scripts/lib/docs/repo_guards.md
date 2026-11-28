# Ratchets and repo-owned guards (`lib.ci.repo_guards`, #854)

The module docstring has the rules and the block shape. Known limits:

- Only GitHub Actions workflows directly under `.github/workflows/` and `.gitlab-ci.yml` are
  read. A GitLab `include:` is not followed. With nothing found, a root `include:`, a tracked
  Prow, Jenkins or other CI file, or a step the follower leaves unsettled (a remote reusable
  workflow, an `apply from:` Gradle script) reads not measured (`ci_not_read`).
- A guard script is known by its name: a check word and a boundary or size word in the same
  file or folder name. A guard named for neither (`hack/verify-gofmt.sh`) is not credited; a
  name that pairs them for another purpose would be.
- A baseline is known by its name (`baseline` or `ratchet`, or `.rubocop_todo.yml`,
  `.betterer.results`). A tool that reads its baseline only from its own configuration
  (`phpstan.neon` `includes:`, `detekt` in `build.gradle`, eslint `max-lines`) is not read.
- The update command for a lint baseline is found by text: a tracked command line that names
  the baseline or its check script beside an update word, in the same command of a line
  (`uv sync && ruff check --baseline x.json` is not an update). A whole-line
  comment (`#`, `//`) is skipped; a trailing comment on a code line, or a string such as an
  error message that names both, still counts.
- A guard script's tests are found by file name, and the test file must name the script's
  stem. For a guard that is a folder (`go run ./dir`, `python -m pkg`), only `test_*.py`,
  `*_test.py` and `*_test.go` inside it or under `tests/<name>/` are joined; a `.bats` or
  `.test.ts` file there is not. Whether CI runs those tests is not read here; `layer-5-ci` reads whether CI runs
  tests at all.
- Check and boundary words match inside a name token (`importcheck`), so a script named for
  another purpose (`validate_import_data.py`) is credited as a boundary checker.
- With no guard found, any step the follower leaves unsettled in any job read makes `present`
  null (`ci_not_read`), not false. That keeps an unread step from reading as no guard, at the
  cost of fewer measured falses; the corpus discrimination run (#842) shows how many.
- An architecture test counts when any ordinary CI job runs a test command, not only the job
  that would run that file.
- `guarded_by` on `repo-guards.1` caps a `verified` grade at `measured` while
  `repo-guard-tested.1` is not met. Both read committed files (`head_structure`, graded
  `measured`), so today the cap moves nothing; it records the join for a grade that later
  reads CI runs.
