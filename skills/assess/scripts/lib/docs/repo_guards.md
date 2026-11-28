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
  the baseline or its check script beside an update word. A line that only mentions both
  (a comment) counts too.
- A guard script's tests are found by file name, and the test file must name the script's
  stem. Whether CI runs those tests is not read here; `layer-5-ci` reads whether CI runs
  tests at all.
- An architecture test counts when any ordinary CI job runs a test command, not only the job
  that would run that file.
- `guarded_by` on `repo-guards.1` caps a `verified` grade at `measured` while
  `repo-guard-tested.1` is not met. Both read committed files (`head_structure`, graded
  `measured`), so today the cap moves nothing; it records the join for a grade that later
  reads CI runs.
