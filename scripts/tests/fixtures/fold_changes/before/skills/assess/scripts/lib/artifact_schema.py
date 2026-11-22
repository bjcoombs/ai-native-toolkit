# - run-context.json coverage_gate gains enforced_meets_minimum and enforced_basis
#   (issue #921): the threshold reading with an enforced CI run of the gate
#   required. A gate's unit gains missed_ratio, and a JaCoCo MISSEDCOUNT /
#   MISSEDRATIO maximum is reported as a gate. Additive: no existing key moves.
ARTIFACT_SCHEMA_VERSION = "1.42.0"

# - ci_checks recognises common test runners and reads unread targets as unknown
#   (issue #920): dotnet test, swift test, mix test, deno test, bazel test, ctest,
#   phpunit, pest, rspec, Gradle test tasks, xcodebuild test, php artisan test and
#   check-named make, just, task, rake, turbo and nx targets run a check, and a
#   wrapper (docker compose run, bundle exec, uv run, poetry run, npx, pnpm exec,
#   hatch run) is read by the program it runs, for Layer 3 lint runs as well as
#   Layer 5. A target whose recipe is not read (make foo, npm run foo,
#   ./scripts/foo.sh, docker run img) reads enforced_check_runs null
#   (check_target) where it read false; setup-named targets (make build) still
#   read false. Layer 5 level 1 can move from 0 to met or unknown.
ASSESS_METHOD_VERSION = 37
