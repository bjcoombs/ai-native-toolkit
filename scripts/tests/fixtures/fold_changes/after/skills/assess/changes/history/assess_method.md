# `ASSESS_METHOD_VERSION` history

Fixture history of `ASSESS_METHOD_VERSION`, newest entry first.

<!-- make fold writes each new entry below this line, newest first. -->

38 (3.0.0 release): two rule changes since 37.
- ci_checks masks a check in an errexit-exempt position before a top-level `exit 0`
  under the default `bash -e` (issue #936): `pytest && echo ok; exit 0`, `pytest | tee
  log; exit 0` without pipefail, an `if` / `while` condition or a `!` pipeline.
  `ruff check . --exit-zero || pytest` drops `pytest`, a folded `run: >` block is read
  folded, a quoted `{` or `<<EOF` no longer hides a later `exit 0`, and `hatch run
  pytest -V` / `--co` are info calls. Layer 3 and Layer 5 level 1 can move from met to
  unmet; a folded pwsh `pytest` / `exit 0` can move from unmet to met.
- agent_consistency drops a leading heading or list ordinal before reading
  threshold numbers (issue #960): `## 2. Coverage` beside a stated 90 is no
  conflict, so agent-consistency level 1 can move from unmet to met. A
  next step for an unlinked system names its primary file (CLAUDE.md) before
  a supplementary rule file.

- ci_checks recognises common test runners and reads unread targets as unknown
  (issue #920): dotnet test, swift test, mix test, deno test, bazel test, ctest,
  phpunit, pest, rspec, Gradle test tasks, xcodebuild test, php artisan test and
  check-named make, just, task, rake, turbo and nx targets run a check, and a
  wrapper (docker compose run, bundle exec, uv run, poetry run, npx, pnpm exec,
  hatch run) is read by the program it runs, for Layer 3 lint runs as well as
  Layer 5. A target whose recipe is not read (make foo, npm run foo,
  ./scripts/foo.sh, docker run img) reads enforced_check_runs null
  (check_target) where it read false; setup-named targets (make build) still
  read false. Layer 5 level 1 can move from 0 to met or unknown.
