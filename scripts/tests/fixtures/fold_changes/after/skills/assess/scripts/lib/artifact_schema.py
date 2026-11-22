# - run-context.json coverage_gate gains enforced_meets_minimum and enforced_basis
#   (issue #921): the threshold reading with an enforced CI run of the gate
#   required. A gate's unit gains missed_ratio, and a JaCoCo MISSEDCOUNT /
#   MISSEDRATIO maximum is reported as a gate. Additive: no existing key moves.
# 1.43.0 (3.0.0 release): three changes since 1.42.0.
# - run-context.json layer_grades gains instruction_presence (issue #821): root,
#   nested_only or none. instruction_file_present is true for nested_only, and
#   instructions_grade there is the best nested grade, where both read as no
#   instruction file before; both are null when there is no root file and the
#   nested_instructions scan did not run. gap_actions gains a layer_grades entry for
#   nested_only and now sits after layer_grades in run-context.json.
# - ci_checks basis, enforced_basis and lint_basis gain external_ci and
#   external_ci_marker (issue #928): under --platform, more than half of the sampled
#   merged pull requests carry a check of a known external CI system; offline, a root
#   Prow OWNERS_ALIASES with no CI file. Either reads null in place of a false case.
#   unread_ci_files counts more paths (LUCI .ci.yaml and infra/config/, every Azure
#   Pipelines layout, Prow, Zuul, Drone, Woodpecker and others; lib/unread_ci.py).
#   Additive: no existing key moves.
# - run-context.json delivery gains sample_page_size (issue #995): the changes per
#   heavy read the merged sample finished at, or null when none was made. Additive:
#   no existing key moves.
ARTIFACT_SCHEMA_VERSION = "1.43.0"

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
# 38 (3.0.0 release): two rule changes since 37.
# - ci_checks masks a check in an errexit-exempt position before a top-level `exit 0`
#   under the default `bash -e` (issue #936): `pytest && echo ok; exit 0`, `pytest | tee
#   log; exit 0` without pipefail, an `if` / `while` condition or a `!` pipeline.
#   `ruff check . --exit-zero || pytest` drops `pytest`, a folded `run: >` block is read
#   folded, a quoted `{` or `<<EOF` no longer hides a later `exit 0`, and `hatch run
#   pytest -V` / `--co` are info calls. Layer 3 and Layer 5 level 1 can move from met to
#   unmet; a folded pwsh `pytest` / `exit 0` can move from unmet to met.
# - agent_consistency drops a leading heading or list ordinal before reading
#   threshold numbers (issue #960): `## 2. Coverage` beside a stated 90 is no
#   conflict, so agent-consistency level 1 can move from unmet to met. A
#   next step for an unlinked system names its primary file (CLAUDE.md) before
#   a supplementary rule file.
ASSESS_METHOD_VERSION = 38
