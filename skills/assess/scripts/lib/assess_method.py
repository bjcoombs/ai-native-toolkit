"""The finding method version every /assess run stamps: which rules decide the findings.

`ASSESS_METHOD_VERSION` is an integer, independent of plugin releases and of
`ARTIFACT_SCHEMA_VERSION`, bumped by one in any change that moves which findings a run
produces on unchanged code. `assess_core` stamps it in run-context.json and in
`findings.json`. Its version history is the module comment. `lib.artifact_schema`
re-exports it, so `from lib.artifact_schema import ASSESS_METHOD_VERSION` still works.

During a version freeze a change to the counter adds a note file under
`skills/assess/changes/` instead of editing the history comment; the release PR folds the
notes in with `make fold` (#1011). It moved here from `artifact_schema.py` in the 4.0.0
release, when the two histories together passed the file-size limit.
"""

# The finding method version: which rules decide the findings a run produces.
# Independent of plugin releases and of ARTIFACT_SCHEMA_VERSION (which versions
# artifact shape, not finding semantics). Bump it, by one, in any change that
# moves which findings a run produces on unchanged code; the CI gate compares it
# with the version in the committed .assess/findings.json and explains the
# finding moves instead of failing on them unexplained.
# 1: the baseline (issue #554).
# 2: churn counts follow renames, moving staleness ratios, lying_map and hotspot
# ranks for files renamed inside the churn window (issue #425).
# 3: the doc-complexity join reads complexity-stats.json's uncapped `files` list,
#    so a complex file outside every top-10 list can now produce lying_map,
#    unexplained_complexity or good_contract (issue #556). The same list reaches
#    candidate_dead_weight (keyhole_signals._high_complexity_paths) and
#    orphaned_understanding (understanding_analysis._extract_file_ccn), which
#    now also see every scored file.
# 4: the complexity join takes doc ownership from the doc-staleness rule
#    (lib.docs.doc_ownership, issue #555): a README beside a design doc owns the
#    directory's code, and a non-base doc owns none by directory.
# 5: a CLAUDE.md identical to its AGENTS.md below the header (H1 and leading
#    HTML comments) is the informational mirrored_instruction_files, not the
#    capping claude_shadows_agents (issue #589).
# 6: on a shallow clone every doc-staleness row is confidence "low", which keeps
#    it out of lying_map and halves its stale-hub priority (issue #544). A full
#    clone's findings do not move.
# 7: instruction files are graded on always-on size and share instead of the
#    whole-file size curve (issue #560), which moves instruction grades.
# 8: a bats suite is a test file and credits its shell script (issue #593), so a
#    co-located ``<stem>.bats`` can now produce self_referential_tests (E2).
# 9: a parallel-tree ``run.bats`` equally close to a ``run.sh`` and an
#    extensionless ``run`` credits neither (issue #635), so a test_focus signal or a
#    hotspot "Has test file" row can move.
# 10: a skill's SKILL.md is a base doc ranked after README.md (issue #614), so it
#     owns its skill directory's code in doc staleness and the complexity join,
#     and can move doc_staleness.modularity (base_doc_dir_ratio).
# 11: a leading HTML comment only one of a CLAUDE.md / AGENTS.md pair carries is
#     compared, not stripped, so it turns a mirrored_instruction_files into a
#     claude_shadows_agents; text after a header comment's ``-->`` (an H1
#     included) is body (issue #632).
# 12: wording and padding inside an instruction-file section no longer lower its
#     penalty (issue #619): command_reference is an absolute fenced count, a size
#     signal beats always-on wording, fetch lines past the relocatable cap count
#     as always-on, and the B cap reads the whole loaded file. Instruction grades
#     can move.
# 13: doc_claims compares pins per (pinned file, tool) (issue #610), so a Node pin
#     no longer contradicts a Python pin in one ``.tool-versions``; the L0
#     "conflicting doc claims" row and scorer evidence can move.
# 14: a tracked source's rival shell spelling no longer hides an untracked
#     ``run.sh`` beside it, so the pair ties for ``run.bats`` (issue #648); and a
#     GitLab exact protected-branch rule combines with every matching wildcard,
#     so a config_drift snapshot of the exact rule can report the wildcards'
#     looser levels as drift; and a rename-only commit (no content change) is
#     no longer churn or a content change (contract I425-2), so doc and code
#     churn, doc ages, staleness ratios and lying_map can move.
# 15: a repository with no importable Python package reports ``structure``
#     ``available: false`` instead of an empty graph (issue #663), so keyhole
#     static metrics and structure_drift tier 1 skip it; findings that read the
#     static graph can move on such repositories.
# 16: a root ``agents/*.md`` shaped as an agent definition is reported in
#     ``instruction_placement.loaded_in_full`` even without a plugin manifest
#     (issue #662), so that list and the scorer evidence drawn from it can move.
# 17: an import graph with no edges reports ``structure.modularity_q`` null with
#     ``modularity_reason: no_import_edges`` instead of 0.0 (issue #682), so the
#     scorer's Layer 2 evidence on such repositories can move.
# 18: a Go module (a go.mod with packages) is graphed from its import blocks
#     (issue #710) instead of reporting no_import_graph_go, so structure, the
#     Layer 2 evidence, and hidden_coupling / refactor_boundary on Go directories
#     can move on Go repositories; structure_drift tier 1 empties its static-derived
#     lists for a graph that maps no module to a file, and the behaviour block projects the
#     static metrics only onto directories in the graphed language.
# 19: TypeScript and JavaScript sources get an import graph (issue #709), and
#     ``select_graph`` measures the dominant source language's graph: a builder
#     that covers it decides with its graph or its failure reason, and another
#     language's graph stands in when no builder covers it or that builder
#     found nothing to graph. Files a builder
#     skips (minified bundles, declarations, tests) do not count toward the
#     dominant language. A TypeScript- or JavaScript-dominant repository now
#     reports ``structure`` available instead of ``no_import_graph_typescript``,
#     so keyhole static metrics, structure_drift tier 1, hidden_coupling and the
#     scorer's Layer 2 evidence can move there.
# 20: on a GitLab remote ``review_reality`` reads each sampled merge request's notes
#     (issue #716), so ``reviewed_share`` and ``bot_review_share`` are measured
#     instead of null and the L7 "hollow required review" row can now fire there.
# 21: every git-history window and day count is counted back from HEAD's committer
#     time instead of today (issue #773): churn windows, ``doc_staleness`` ages and
#     window ratios, instruction ``freshness_days`` (and so the grader's freshness
#     penalty), ``ai_usage`` counts and the keyhole velocity clock. On a repository
#     whose HEAD is old, ages shrink and windows cover the history before HEAD.
# 22: ``structure.front_door_ratio`` is null when no import crosses a package
#     boundary (issue #760), so a single-package repository no longer reads as
#     modular through a vacuous 1.0: ``hidden_coupling`` there rests on
#     modularity alone, and a ``structure.front_door_ratio`` criterion is unknown.
# 23: ``ci_checks`` no longer counts a bare list item that only names an
#     always-check program (``- pytest``, ``- tox``) unless its parent key is a
#     GitLab ``script:`` / ``before_script:`` / ``after_script:`` (issue #725), so
#     a workflow whose only mention is ``needs: - pytest`` or a ``matrix:`` axis
#     ``- tox`` reads ``check_commands`` 0 and Layer 5 level 1 can drop to 0.
# 24: GitLab includes resolve offline (issue #766): ``ci_checks.test_or_lint_runs``
#     reads a check in an included local file or a template cloned in the fleet root
#     (true), or false when every include resolved without one, where it was null;
#     ``review_automation`` credits a review component inside a resolved template;
#     a workflow whose ``on`` key reads ``true:`` (YAML 1.1) has its events read, so
#     an issue_comment-only review action is no longer credited.
# 25: ``agent_environment`` adds ``setup_ready`` (the setup half of ``layer5_cap``)
#     and ``pinning.unlocked_total`` (issue #786). Layer 5 level 2 reads
#     ``unlocked_total eq 0`` instead of the cap: a repository whose instruction
#     file names no check can now read level 2, and one with committed setup
#     but an unlocked manifest no longer can. ``layer5_cap`` is unchanged.
# 26: Layer 4 level 1 reads structure_drift.tier_0.boundary_patterns instead of
#     matched_patterns (issue #785), so a CODEOWNERS whose lines own no module
#     source (`*`, `*.py`, `/.github/`, `README.md`, `docs/`) no longer credits a
#     declared boundary.
# 27: under --platform (and in a full /assess run) GitLab's CI lint API gives the
#     merged CI configuration of a repository whose includes did not all resolve
#     offline (issue #796): ``ci_checks.test_or_lint_runs`` reads true or false
#     where it was null, and ``review_automation`` credits a review component the
#     merge pulled in. Offline headless runs are unchanged.
# 28: a masked CI check (``|| true``, ``continue-on-error: true``,
#     ``allow_failure: true``, ``set +e`` without a re-raise) or one that does
#     not run on ordinary changes (a ``workflow_dispatch``-only workflow, a
#     manual GitLab job) no longer meets Layer 5 level 1 (issue #775): it reads
#     ``ci_checks.enforced_check_runs``, so the Layer 5 row and the scorer's
#     Layer 5 evidence can move.
# 29: coverage_gate reads go-test-coverage's .testcoverage.yml, cargo tarpaulin
#     --fail-under and cargo llvm-cov --fail-under-lines (issue #788), and a
#     repository holding a go.mod or Cargo.toml with no gate found reads Layer 6
#     level 1 as not measured (threshold_meets_minimum null) where it read 0.
# 30: a self_referential_tests path whose done review is recorded in the committed
#     .assess/actions.json (status done, completed_sha an ancestor of HEAD, the path
#     in files) and untouched since that commit leaves the attention list,
#     prescribed_actions and the keyhole summary (issue #721), so the Top 3 can move.
#     It stays in derived_findings with a reviewed note; a later change re-flags it.
# 31: ``ai_usage`` buckets an account confirmed in the ``[identities]`` section of
#     ``.assess/config.toml`` or ``--fleet-config`` (issue #765) before any pinned
#     rule: a confirmed agent counts in ``agent_authored_commits``, a confirmed bot
#     or person never does. The same matcher serves ``delivery`` and the GitLab
#     note classifier (``gitlab_notes``) under --platform, so a confirmed account
#     moves their agent counts too. Without the section nothing changes.
# 32: Layer 3 level 1 reads ``ci_checks.lint_runs`` (issue #782): a linter run
#     in CI, not a suppression comment. One suppression comment no longer earns the row,
#     and a clean repository that lints in CI reads at least 1 where it was
#     unknown, so the Layer 3 row can move.
# 33: a repository with no readable CI check whose tree tracks a CI file of a
#     system the scan does not parse (Jenkins, CircleCI, Azure Pipelines,
#     Bitbucket Pipelines, Travis CI, Buildkite) reads
#     ``ci_checks.enforced_check_runs`` null (``basis: unread_ci_system``), so
#     its Layer 5 row moves from level 0 to unknown (issue #787).
# 34: the displayed headline changes (issue #747): the layered "X / 8" score and
#     its band names are retired for the deterministic Step, its next unlock and
#     "Verified practices: N of M measured"; the badge and the log.md entry carry
#     the Step and the tally, and the scorer returns per-layer verdicts only.
# 35: the guardrails block and its stated_pct signal (issue #767): the default
#     maturity model gains the guardrails-stated row (half of the configured
#     gates stated is level 1, all of them level 2, none found is unknown).
#     Existing rows are unchanged.
# 36: the agent_consistency block and its conflicts and unlinked signals (issue
#     #768): the default maturity model gains the agent-consistency row (no
#     conflict is level 1, every system linked to the canonical file level 2,
#     fewer than two systems unknown). Existing rows are unchanged.
# 37 (2.2.0 release): four rule changes since 36.
# - the empty-set rule and two requires (issue #906): a file-size condition
#   reads not measured (no_scored_files) when no file is scored, layer-2-design.2
#   requires layer-2-design.1 and layer-4-architecture.2 requires
#   layer-4-architecture.1. Fewer conditions count on docs-only repositories
#   and tangled import graphs; no shape changes. The default maturity model's
#   version moves with it.
# - ci_checks closes five false-credit holes (issue #919). A version or usage
#   query (`ruff --version`) runs no check; an exit-zero flag (`--exit-zero`,
#   `--issues-exit-code=0`), a later top-level `exit 0` in a GitHub run: body
#   without errexit (pwsh, a Windows runner, set +e) and a literal `if: false`
#   step or job are masked; a workflow in a subfolder of .github/workflows/ is
#   not a CI file. Layer 3 level 1 and Layer 5 level 1 can move from met to unmet.
# - Layer 6 level 1, and so the Step 4 coverage_gate gate, reads
#   coverage_gate.enforced_meets_minimum (issue #921): a threshold of at least 50
#   percent that no enforced CI line runs is level 0, where it read 1, and CI the
#   scan cannot read is unknown. A JaCoCo MISSEDCOUNT maximum reads unknown, not 0.
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
# 38 (3.0.0 release): 22 rule changes since 37.
# - the displayed headline changes (issue #844): the tally line reads "Practices in
#   place: N of M measured, V verified" instead of "Verified practices", the Next
#   line's shortfall reads "N more practices are in place", a per-tag strip line
#   follows the tally with NOT MEASURED, HAZARD and DECLINED badges, and the badge
#   and log.md summary read "Step S · N in place of M measured".
# - structure_drift.tier_0.boundary_patterns counts an architecture-doc reference
#   only when its resolved files hold module source under a non-dot directory, the
#   CODEOWNERS predicate ownership_parser.is_boundary_pattern (issue #874). An
#   ARCHITECTURE.md naming only `CONTRIBUTING.md`, `docs/` or `.github/workflows/ci.yml`
#   no longer meets Layer 4 level 1, which can move from met to unmet.
# - GitLab `rules:` are read as GitLab runs them (issue #884): the first matching rule
#   decides, `if:` expressions are evaluated per merge request and branch push pipeline,
#   and a top-level `workflow: rules:` gates every job of its file. A job gated by
#   `$CI_PIPELINE_SOURCE == "schedule" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH` or
#   `$CI_COMMIT_TAG && ...` no longer runs a check (enforced_check_runs can move from
#   true to false); a custom variable, inline, anchored or multi-line rules and `exists:`
#   read unknown. Layer 5 level 1 can move from met to unmet or unknown.
# - the empty lockfile set (issue #927): layer-5-ci.2 reads not measured
#   (no_manifests) when agent_environment.pinning.manifests_total is 0, where it
#   read met. Fewer conditions count on repositories with no dependency manifest.
# - the lockfile check reads Mix, Pub, SwiftPM, .NET, Gradle, vcpkg and Conan
#   manifests (issue #927 part B). Libraries that by convention commit no lock
#   (Composer type library, a published Pub package, a SwiftPM library) are
#   left out of the count; a Gradle version catalog or NuGet central package
#   management reads unlocked. layer-5-ci.2 can move from met to unmet on
#   repositories with those manifests, and from unmet to not measured on
#   library-only ones.
# - ci_checks masks a check in an errexit-exempt position before a top-level `exit 0`
#   under the default `bash -e` (issue #936): `pytest && echo ok; exit 0`, `pytest | tee
#   log; exit 0` without pipefail, an `if` / `while` condition or a `!` pipeline.
#   `ruff check . --exit-zero || pytest` drops `pytest`, a folded `run: >` block is read
#   folded, a quoted `{` or `<<EOF` no longer hides a later `exit 0`, and `hatch run
#   pytest -V` / `--co` are info calls. Layer 3 and Layer 5 level 1 can move from met to
#   unmet; a folded pwsh `pytest` / `exit 0` can move from unmet to met.
# - issue #937, two false readings. A coverage gate below 50 percent beside a
#   go.mod or Cargo.toml with no gate read for that language reads
#   threshold_meets_minimum null (go_rust_gate_form_not_read) where it read
#   false, so Layer 6 level 1 and the Step 4 coverage_gate gate read unknown,
#   not unmet. A CI check or lint step that runs a committed script no tracked
#   file matches (run: ./ci.sh with no ci.sh) reads enforced_check_runs and
#   lint_runs null (script_not_found) where it read true: Layer 3 and Layer 5
#   level 1 can move from met to unknown.
# - `ci` is no longer a trusted check word (issue #951): `make ci`, `npm run ci`, `pnpm ci`,
#   `yarn ci` and `./ci.sh` can run a build, a deploy or a test suite, and the recipe is not
#   read, so a CI line naming only one reads null with basis `check_target`, like `make foo`;
#   so does a `ci` name behind `python`, `source`, `cargo`, Gradle, Maven or `go`
#   (`python ci.py`, `cargo ci`, `./gradlew ci`), never a false 0.
#   Layer 5 level 1 (`enforced_check_runs`, the Step 3 `ci_runs_tests` gate) and Layer 3
#   level 1 (`lint_runs`) can move from met to not measured. `bun ci` and `npm ci` stay
#   installs, and a name that also carries a check word (`make ci-test`, `npm run test:ci`)
#   still counts. Instruction files keep `ci` (`lib.named_check`): a CLAUDE.md that names
#   `make ci` still counts at Layer 0 (`agent_environment.check.named_total`), unchanged.
#   A committed `./ci.sh` was credited by its name (`lib.ci_scripts` checks that the file
#   is there and never reads its body), so it now reads null `check_target` too; a missing
#   one keeps `script_not_found` (#937), and a masked one keeps `not_enforced`.
# - issue #954, coverage-gate CI credit edge cases. enforced_meets_minimum (Layer 6
#   level 1, the Step 4 coverage_gate gate) no longer credits a CI run that does not
#   enforce the gate: a threshold named only in echo or printf text beside a run with
#   another threshold, pytest with --no-cov (on the line or in addopts), a Gradle run
#   that excludes jacocoTestCoverageVerification (or test, leaving it no execution data)
#   with -x or --exclude-task, and a Gradle dry run (-m, also in a cluster such as -qm,
#   --dry-run, --task-graph) now read false. pytest -c FILE reads FILE's addopts as the
#   run's own, and reads null when FILE was not read. A pytest addopts --cov or Jest
#   collectCoverage: true switches on only the gates in its own directory or below, so a
#   sibling package's config gate reads false or null where it read true. A threshold
#   outside a pytest section of pyproject.toml or setup.cfg (a hatch, pdm or poe script, a
#   tox command) is a task-runner gate: indirect_run when CI runs hatch run, pdm, poe (also
#   through uv run or poetry run) or tox, and not_run_in_ci when it runs none, where it
#   read unread_ci_system. pdm run is read through to the program it runs, so pdm run
#   pytest --cov credits a coverage.py gate where it read not_run_in_ci. A second
#   --cov-fail-under on one CI line is read as its own gate.
# - guardrails reads the run scope (issue #956): a scoped run walks the scope
#   only, as coverage_gate does, and a scope root with no ruff, flake8 or mypy
#   config is gated by the tool's nearest config at or above it, as each tool
#   resolves it (ruff from each file, mypy and flake8 from the directory they
#   run in). Ruff's C901 selection resolves select and ignore by specificity (C9
#   ranks with C90, an ignore wins a tie), so `select = ["C901"]` beside
#   `ignore = ["C"]` is a complexity gate. A threshold states a gate only as a
#   standalone number (no letter, digit or underscore on either side, no dot
#   before it, so "80th" and "15x" state nothing), and a
#   coverage floor's ratio form needs a decimal point, so "step 1" and "v1.0" no
#   longer state a floor of 100.
# - CI reader edge cases (issue #958): a GitHub workflow whose `on:` block list sits at
#   the key's own column (`on:` then `- push`) is read, so ci_checks.enforced_check_runs
#   can move from null (`unknown_trigger`) to true or false. A top-level GitLab job named
#   `trigger` after a blank line is no longer read as a child pipeline, so a merged
#   configuration with no check can move test_or_lint_configured from null
#   (`merged_config_partial`) to false. A lint-API include source with surrounding
#   whitespace is recorded once.
# - agent_consistency drops a leading heading or list ordinal before reading
#   threshold numbers (issue #960): `## 2. Coverage` beside a stated 90 is no
#   conflict, so agent-consistency level 1 can move from unmet to met. A
#   next step for an unlinked system names its primary file (CLAUDE.md) before
#   a supplementary rule file.
# - The two 2.1.0 signal paths renamed in 2.2.0 load again (issue #970):
#   a `--levels` model naming `ci_checks.test_or_lint_runs` or
#   `coverage_gate.enforced` reads `ci_checks.test_or_lint_configured` or
#   `coverage_gate.configured` and scores the same, with one deprecation line
#   on stderr per old path, where it exited 2. Fleet compare reads both sides
#   under the new paths, so a 2.1.0 run against a 2.2.0 run lists no added or
#   removed leaf for them. `SIGNAL_ALIASES` moves to `lib/signal_aliases.py`.
# - issue #1008, a check inside a ( ) subshell that ends in a no-op is masked. A
#   || true, || :, || /bin/true or || exit 0 inside a subshell masks the segments
#   before it back to the subshell's start, as at top level, so (pytest || true) and
#   (cd sub && pytest || true) read as masked, not enforced: enforced_check_runs and
#   lint_runs (Layer 5 and Layer 3 level 1) and the coverage-gate CI run can move from
#   met to unmet. (pytest && echo ok) and pytest && (echo x || true) keep their check.
# - ci_checks.enforced_check_runs resolves a GitLab job's same-file `extends:` chain
#   (a name or a list, multi-level, the last parent winning and the job's own keys
#   overriding) before it reads `rules:`, `when:`, `only:` and `except:`, and reads
#   `except:` with `only:` semantics (issue #1021). A job inheriting schedule-only or
#   tag-only rules from a hidden template no longer counts as running on ordinary
#   changes, and a job whose `extends:` target is not in the file (an `include:`),
#   a cycle, or a job given `rules:` beside `only:` / `except:` reads null. Layer 5
#   can move from met to unmet or unknown; no reading moves to met.
# - A flow-map `only:` or `except:` (`only: {refs: [tags]}`) reads null instead of
#   having its map keys read as ref names (issue #1021); an `only: {refs: [tags]}` job
#   moves from running on ordinary changes to unknown.
# - three rare lockfile readings (issue #1025): a SwiftPM package with a
#   `.library(` product and no `.executable(` product reads skipped_library even
#   when it declares an `.executableTarget(` (as a library that ships a build-tool
#   or command plugin does), where it read an unlocked app; a Kotlin
#   Multiplatform `commonMain.dependencies { ... }` block counts the build, where
#   it gave no row; a `package.json`, `composer.json` or `vcpkg.json` with a
#   byte-order mark parses, where it gave no row. layer-5-ci.2 can move from unmet
#   to not measured on such Swift packages, and from met or not measured to
#   unmet on repositories with the other two.
# - On a CI line the `.` builtin runs a script like `source` (issue #1033); instruction files
#   (Layer 0) read `.` as before. `. ./test.sh` read as no check, a
#   false 0; it now reads as `source ./test.sh` does, at Layer 5 (`enforced_check_runs`) and
#   Layer 3 (`lint_runs`). A sourced script the tree does not hold reads `script_not_found`
#   (null), as `./x` does since #937, where `source ./test.sh` with no file was credited.
#   Any other sourced script whose body is not shown reads null (`check_target`), not false
#   (`source ./scripts/common.sh`). Sourcing an environment (`. venv/bin/activate`, `source
#   .env`, `. ./env.sh`, `. ~/.nvm/nvm.sh`) runs no check and stays false.
# - A committed CI script's body is read where it is tracked at the exact path the line runs,
#   and for a `./x` run only when git records it executable (mode 100755) (`lib.script_body`): a body that provably fails when its check fails (`set -e` before the
#   check, the check as the last top-level statement, or `|| exit 1` after it) reads true,
#   so an executable `./ci.sh` ending in `pytest` moves from not measured to met. A body the reader does not
#   model, or one whose check cannot fail the script, keeps the name's reading. The only
#   reading that moves down is the missing sourced script above.
# - A target whose name pairs `ci` with a setup or build word (`make deploy-ci`, `npm run
#   build:ci`, `make ci-deploy`, `./scripts/deploy-ci.sh`) reads false (no check), not null,
#   unless its body shows a check.
# - One `on:` reader for GitHub workflows (issue #1036): shared_tooling.workflow_events
#   now reads through ci_triggers.workflow_events. A column-0 `on:` list (`on:` then
#   `- issue_comment`) was unread and so credited as running on changes; it is now
#   read, so review_automation can move from credited to not credited for a review
#   action wired only to `issue_comment`, `issues` or `workflow_dispatch`. Nested list
#   items (`branches: - main`, `types: - created`) are no longer read as events. A flow
#   list or mapping that runs onto later lines (`on: [workflow_dispatch,` then
#   `push]`) is joined in both readers, so ci_checks.enforced_check_runs can move from
#   false to true and review_automation from not credited to credited.
# - ci_triggers moves on flow collections (issue #1036): a flow list or mapping opened on
#   the `on:` line and continued below (`on: {` then `issue_comment: {}` then `}`) reads
#   its events where it read `unknown`, so ci_checks.enforced_check_runs can move from
#   null to true or false; one that never closes (invalid YAML) reads `unknown`, where
#   it read its first line's events, so enforced_check_runs can move to null. Such an
#   unclosed `on:` is never credited by review_automation (`ci_triggers.unclosed_on`).
# 39 (4.0.0 release): 53 rule changes since 38.
# - issue #799, a CI command continued with a trailing backslash is joined onto its first
#   line before it is read, so mvn ${MVN_ARGS} -T8 \ ... verify reads verify and credits
#   test_or_lint_configured and enforced_check_runs (Layer 5 level 1), where it read
#   no_check_command. A GitHub ${{ ... }} expression reads as one word, and a shell
#   expansion before a make, mvn, gradle, go or cargo goal is not read as the goal.
# - issue #799, a Maven run with -DskipTests or -Dmaven.test.skip and only lifecycle phases
#   (mvn -DskipTests verify) reads enforced_basis check_target (null) on a CI line, where
#   it read true. With no other check, test_or_lint_configured reads null with the new basis
#   value check_target, since verify may still run a lint plugin. A plugin goal beside it
#   (checkstyle:check) is read as before.
# - issue #799, a run, script or command input under a uses: step's with: reads as a shell
#   run only when the action runs it: a local action whose committed action.yml has a run:
#   step of exactly ${{ inputs.<name> }}, or nick-fields/retry, reactivecircus/
#   android-emulator-runner, coactions/setup-xvfb, gabrielbb/xvfb-action,
#   uraimo/run-on-arch-action and the vmactions/<os>-vm family. An
#   actions/github-script script is not read. Any other action's input still counts in
#   test_or_lint_configured but reads check_target (null) for enforced_check_runs and
#   lint_runs, where it read true.
# - issue #799, xvfb-run [flags] and timeout [flags] DURATION are read through to the
#   program they run, and a committed CI script reached through a tracked symlink that
#   leaves the checkout is never read.
# - Doc freshness leaves generator-directive pages out of the staleness share (issue #816):
#   a mkdocstrings `::: pkg.mod` stub renders from code at build time, so it leaves
#   doc_staleness.summary.measured_docs and no longer reads as a stale hand-written doc.
#   A hand-written page that embeds one directive is still measured.
# - A parallel docs/ page maps to one production module (issue #817): never to test or
#   fixture code, by its stem (a module file or directory) before its parent directory's
#   name, with ties broken by the longest shared path suffix, then depth, then path. A page
#   that once mapped to a mirrored tests/ directory or to a whole parent-named sibling
#   directory is now measured against the module it documents, or falls back to its
#   explicit links or the repo baseline when only test code matches.
#   A stem common to many modules (setup, config, main) matches only a directory, and a
#   top-level docs/ page never maps to the docs/ directory itself.
#   A module file that shares only its stem with the page yields to the page's explicit
#   code links.
# - Layer 4 level 2 measures file size over production source, not over every scored file
#   (issue #820): stats_summary.loc_by_class.source.p95 replaces stats_summary.loc.p95, and
#   the test set is reported apart in stats_summary.loc_by_class.test.
# - observability.rung reads instrumentation from code as well as manifests, and the
#   runbook name pattern no longer matches ordinary words (issue #823). A Python
#   `logging.getLogger`, Go `log/slog`, `java.util.logging`, SLF4J, Android `Log`, Apple
#   `OSLog`, Erlang/Elixir `:telemetry` or `ActiveSupport::Notifications` call in
#   production source counts as instrumented, as do the Rust `tracing` and `log` crates in
#   a Cargo.toml runtime dependency table and `mix.exs`, `Package.swift`, `pubspec.yaml`
#   and `gradle/libs.versions.toml` manifests. Manifest names match whole (no letter or
#   digit either side), so `hotel-booking` no longer reads as OpenTelemetry. A manifest,
#   logger or config under a test, fixture, example, vendored or generated path
#   (lib/path_classes.py) is not credited, and a doc under a test or fixture path is not a
#   runbook. On-call in a doc name needs a word or path-segment boundary, so
#   `function-call-in-default-argument.md` is not a runbook. The rung can move either way.
# - Promissory markers set aside unambiguous fixture trees (issue #824): testdata/, test_data/,
#   test-data/, __fixtures__/, __snapshots__/, mdtest/ and resources/test/ at any depth, and
#   fixture/, fixtures/ or snapshots/ below a test directory, not only tests/fixtures/. A
#   production tree named like one (core/snapshots/, a packages/fixtures package) still counts,
#   and a scoped run reads the path relative to the unit. A linter's fixture noqa no longer
#   counts toward promissory_markers.families.suppression.total or .stale (Layer 3 levels 1
#   and 2), so both can fall on a repository with fixtures outside tests/fixtures/.
# - issue #859, GitLab `project:` and `component:` includes match the ghsync clone ignoring
#   case, as GitLab paths do. `project: Grp/CI-Templates` reads the clone `grp/ci-templates`
#   on Linux too, where it read `not_cloned` (and `test_or_lint_configured` null); macOS and
#   Windows already found it. Two clones differing only in case are ambiguous and read
#   `not_cloned`.
# - A monorepo run with two or more units shows the unit roll-up under the headline (issue
#   #865): the lowest unit's Step with the measured units at the next Step, then the change
#   that moves the most units to their next Step, a change at the root ranked first on a tie.
#   The repository's Step and headline are unchanged; a single-unit run renders as before.
# - coverage_gate reads Codecov status targets (issue #882, lib/testing/coverage_codecov.py): each
#   coverage.status.project / .patch status in the first codecov.yml Codecov reads is a gate
#   with tool codecov. A project `target: auto` (or no target) makes
#   threshold_meets_minimum null with unmeasured_reason `codecov_target_not_read` where it
#   read false; a numeric project target counts (80% is a gate at 80) and
#   enforced_meets_minimum reads null with enforced_basis `status_check_unconfirmed` where
#   it read false, since whether branch protection requires the status is not read. A
#   patch status is left out of threshold_meets_minimum and of the CI-run reading (an
#   `auto` target is left out of the CI-run reading too, so neither nulls an unrun floor's
#   false), so the
#   other gates decide (project 20% beside patch 100% reads false); when patch statuses
#   are the only gates, the reading is null with `codecov_patch_only` where it read false.
#   Repositories with no Codecov status section are unchanged.
# - Name-only pinned agent matches are hints that need confirmation (issue #883, decision on
#   #883 of 2026-11-22). A pinned match with no `[bot]` suffix or verified app identity
#   (`noreply@anthropic.com`, `copilot@` addresses, the `copilot` login, `cursoragent@cursor.com`,
#   OpenHands' address and `openhands-agent`) no longer counts as an agent author until it is
#   confirmed in `[identities]` or matched by an `[[ai_usage.agent_identities]]` rule:
#   - `ai_usage.agent_authored_*` and `agent_either_*` can fall, and
#     `identities_awaiting_confirmation` can rise (new `pinned_agent_hint` evidence);
#     `covered_author_agents` names only the `[bot]` apps, and `limits` gains one line.
#   - `delivery.agent_authored_share` reads null while a sampled change author is an
#     unconfirmed name-only login (such as `copilot`); `delivery.unconfirmed_ai_accounts`
#     counts those accounts.
#   - GitLab notes: a note by a `delivery.AI_REVIEWERS` username or a hinted login leaves AI
#     review undecided (and a human comment it precedes), so `delivery.ai_review_share`,
#     the human comment figures and `review_reality`'s GitLab shares read null until the
#     account is confirmed, where they read a number. A note by an account confirmed as a
#     bot is no longer a human comment (#765 semantics).
#   - Unchanged: `[bot]`-suffix apps, AI co-author trailers, GitHub AI review (a bot-gated
#     `AI_REVIEWERS` login) and the Copilot coding agent's commit address
#     `198982749+Copilot@users.noreply.github.com`, now pinned by its GitHub id in
#     `AGENT_RULES` (it counted before through the `copilot` login rule).
# - Under a caller's own maturity model whose Step 1 has a gate the repository does not meet,
#   the headline now carries the Next line naming Step 1 and its gates (#889). The default model
#   is unchanged: its Step 1 has no gate.
# - Below the first Step of a caller's own model whose Step 1 has a gate, the executive summary
#   reads `Not yet at Step 1 of M.` with a Next line and a call to action, instead of nothing (#889).
# - Under a caller's own model, a Step whose name differs from the default model's now reads
#   the model's own Step name in the executive summary, not the default ladder sentence (#889).
# - The badge of a measured repository below Step 1 takes Step 1's colour, not the not-measured
#   grey (#889).
# - issue #893, a known lint action step whose literal with: input makes it install only is
#   no lint run: astral-sh/ruff-action and chartboost/ruff-action with args: --version (or
#   -V, --help, version, help), and golangci/golangci-lint-action at v9 or later with
#   install-only: true, read lint_runs false (no_lint_command) where they read true. A ruff
#   args or golangci install-only value set but not settled (a ${{ }} expression, an empty
#   args, an install-only value other than true or false, install-only: true on a SHA or
#   branch ref) reads check_target (null). A value whose only expression is one
#   ${{ matrix.<key> }} is read per value of the job's literal matrix axis: any leg that
#   lints keeps the credit, all legs install only read false. install-only: false,
#   install-mode, only-new-issues, a golangci ref before v9 (where install-only is not an
#   input) and a with: the reader cannot read (a merge key, a quoted key, an alias) keep the
#   credit. Layer 5 and test_or_lint_configured do not move.
# - issue #893, a reviewdog/action-* step reads lint_runs null (check_may_pass) where it read
#   true: every listed action defaults to fail_level none and fail_on_error false, so the step
#   never fails its job. Only a literal fail_level of any, info, warning or error keeps the
#   credit; fail_on_error: true alone reads null (reviewdog ignores it when a fail level is
#   passed), apart from action-tfsec, which passes only -fail-on-error. An expression or
#   another casing (Error) reads null.
# - issue #893, a folded (- >, run: >) or plain multi-line CI scalar is read as the one
#   command YAML makes of it: a GitLab item - > / ruff / --version runs ruff --version and
#   is no lint run. A literal block (run: |) still reads one command per line.
# - issue #923, a GitHub step that uses a local composite action (uses: ./.github/actions/x)
#   is read through the action's run: steps, one level and repo-local only, never executed:
#   a composite that runs ruff check . and pytest credits test_or_lint_configured,
#   enforced_check_runs and lint_runs, where it read no_check_command. The caller's
#   continue-on-error and if: false mask every copied step, and each step keeps its own
#   shell:, continue-on-error and || true masking. ${{ inputs.<name> }} in a copied run: takes
#   the caller's literal with: value or the action's literal default; one left in command
#   position, and a nested local action (not followed), read check_target (null). A run: whose command is
#   ${{ matrix.<key> }} is read once per literal matrix value (include and exclude applied),
#   so cmd: ['ruff check .', 'pytest'] is credited; a matrix command that cannot be expanded
#   (a fromJSON matrix, an unset key) reads check_target (null) on all three signals where
#   it read false. A block-list matrix value such as - ruff check . that a step runs is no
#   longer counted again outside that step.
# - Shell, make and hatch commands are parsed per program (issue #955, `lib.command_flags`), at
#   Layer 3 (`lint_runs`) and Layer 5 (`enforced_check_runs`):
#   - A shell's `-e` takes no value, so `sh -e ./ci/run.sh` and `bash -e scripts/test.sh` run
#     their script (false before; now null and true). `-o`, `--rcfile` and `--init-file` take one.
#     `--norc` is not `-c`.
#   - `bash -c "<cmd>"` reads its string as the command when it is one plain command (`bash -c
#     "make test"` reads true). A list, pipeline, redirection or substitution in the string, or
#     a string that cannot be split, reads null rather than false.
#   - make's `-j N`, `-l N`, `-o` and `-W` values are no longer targets, for checks and lint
#     alike, so `make -j 4 build` reads false (was null) and `make -j 4 lint` is a lint run.
#   - A make run whose recipes cannot fail it runs no check and no linter: a dry run (`-n`,
#     `--dry-run`, `--just-print`, `--recon`, `-t`, `-q`, or a cluster such as `-kn`) or
#     `-i` / `--ignore-errors`. Real make exits 0 with a failing recipe, so `make -n test` moves
#     from met to false. `-k` still exits 2 and still counts.
#   - `hatch run lint` has the name trust of `make lint`: it is a lint command and a lint run
#     (Layer 3 true, was null).
# - Layer 0: instruction files are read by the same classifier, so a CLAUDE.md that names
#   `bash -e scripts/test.sh`, `bash -c "make test"` or `bash --rcfile rc ./test.sh` now names
#   a check (`agent_environment.check.named_total` can move from 0 to 1), as CI reads it.
# - issue #1008, a check whose failure the step may hide reads not measured. On a CI
#   line the scan reads as a check, a || fallback that can pass (pytest || echo failed),
#   a check before && with more of the step after it, and a check inside a multi-line
#   { } group or function body called before && or in a condition read
#   enforced_check_runs and lint_runs (Layer 5 and Layer 3 level 1) null with the new
#   basis check_may_pass, where they read true, and the coverage gate's CI run reads
#   enforced_meets_minimum null with enforced_basis check_may_pass. || false, || exit 1,
#   a check as the last statement and a plain group or function call keep their credit.
#   The masking rules also cover if, while and until conditions and one-line groups and
#   functions, but the scan reads no check on a line that opens with if, while, until,
#   { or f(), so those read as before.
# - issue #1008, a GitHub ${{ ... }} expression on a run line holds no shell operator:
#   pytest -n ${{ matrix.workers || 'auto' }} is no longer split at its ||, so the new
#   fallback rule does not read it as pytest || 'auto' (it stays credited).
# - A scoped coverage_gate run reads the root coverage.py config (issue #1032): a Python
#   scope with no coverage.py config of its own counts the root `fail_under` when CI runs
#   coverage from the repository root (or names the root config with `--rcfile` /
#   `--cov-config`), so coverage_gate.threshold_meets_minimum and enforced_meets_minimum
#   can move from false to true. When where CI runs coverage is not resolved and the root
#   floor would change the reading, threshold_meets_minimum reads null with
#   unmeasured_reason `root_coverage_config_unresolved`, where it read false. Whole-repo
#   runs are unchanged.
# - `instruction_placement.loaded_in_full` lists an agent or `SKILL.md` file over 200 lines
#   or over 5000 estimated tokens, most tokens first (issue #1045); it was over 200 lines,
#   longest first, so a long-line file under 200 lines (about 20k tokens in this repository)
#   was left out. Informational: no condition or Step reads it.
# - a new counted condition, `skill-disclosure.1` (issue #1045): every SKILL.md and agent
#   definition within 500 body lines and 5000 estimated tokens, with a name, a description
#   of 1024 characters or fewer, and references that resolve one level deep. Not measured
#   (`no_skill_files`) with none. It gates no Step, so no Step moves; "Practices in place"
#   can.
# - a new counted condition, `contributor-files.1` (issue #1045, tag `docs`): a CONTRIBUTING
#   guide and a SECURITY policy (or a link to GitHub private vulnerability reporting) at the
#   root, in `.github/` or in `docs/`, or the owner's shared policy in its `.github` repository
#   (read with gh); with none in the tree and the owner's not read, the policy is not measured
#   (`org_policy_not_read`), never missing. Not measured (`knowledge_base`) for a knowledge base.
#   It gates no Step.
# - a new counted condition, `skill-script-tests.1` (issue #1045, tag `behaviour`): every skill
#   that holds scripts has tests beside them in its own folder (presence, not coverage; tests
#   only outside the folder are named but do not count). Not measured (`no_skill_scripts`)
#   with no skill holding a script. It gates no Step.
# - a new counted condition, `skill-personal-data.1` (issue #1045, tag `governance`): no skill
#   or agent file holds an email, phone number, home path, IP address, SSH login or key (names
#   are not detected; a credential-shaped assignment is counted for information only). Not measured (`no_skill_files`) with none. It gates no
#   Step.
# - issue #1046, Gradle dry runs and exclusions and pdm / poe read-through on the CI reader.
#   ./gradlew -m check (and -qm, --dry-run, --task-graph) runs nothing: it reads no check and
#   no lint run where it read enforced_check_runs true and may_lint. -Pm and -Dmode=x are no
#   dry run. A task named after -x / --exclude-task is not run (check -x check, test -x test
#   and ktlintCheck -x ktlintCheck read false), and check -x test reads null (check_target),
#   as mvn -DskipTests verify does. pdm run <program>, pdm [run] <script> and poe <task> are
#   read like hatch run: pdm run ruff check . is a lint run and pdm run pytest a check, where
#   both read nothing. A [tool.pdm.scripts] entry that shadows the program reads null in
#   ci_checks and indirect_run in coverage_gate, and a bare pdm <word> with no such script
#   reads false (pdm exits 2). The Gradle and pdm parsing is one module, lib.runner_args,
#   shared with the coverage gate.
#   pdm scripts are read from every tracked pyproject.toml; with none tracked, pdm run <word>
#   and poe <word> read null (check_target), since pdm run test falls back to the shell's test.
# - issue #1061, the CI-line reader reads five forms by what the shell runs. A line that
#   opens a compound command is read from the command it runs: (pytest), if ! pytest; then
#   exit 1; fi, { pytest; } and f() { pytest; }; f read enforced_check_runs true where they
#   read no check, and if pytest; then echo ok; fi, while and until loops that break and
#   groups called before && read null (check_may_pass), as the #1008 masking rules say.
#   In a GitHub bash or sh run: body with pipefail off (the default bash -e {0}, shell: sh),
#   a check piped into another command (pytest | tee log), or inside a { } or ( ) group piped
#   on ({ pytest; } 2>&1 | tee log), is masked, since the step takes
#   the last command's status: it reads false (not_enforced) where it read true, or null
#   (check_may_pass) when a later statement reads PIPESTATUS or re-raises a status.
#   shell: bash, set -o pipefail and GitLab keep the credit. A function no statement of
#   its run: body calls (f() { pytest; } alone, or named only in echo f) is masked too;
#   one named only as another command's argument (retry 3 f) reads null. dash ci.sh runs a script
#   as sh ci.sh does. A || fallback is judged by the commands that run, not its words:
#   pytest || echo false, pytest || echo exit and pytest || echo failed || exit 1 read
#   null (check_may_pass) where they read true.
# - The headline and badge name gates, never a count (issue #1083): `headline.summary`, the
#   badge message and the log.md readiness line read "Step N · next: <gate>" or "Step N · all
#   gates held"; the Next line names blocking gates only; the tally line is relabelled
#   "Catalogue coverage". `HEADLINE_VERSION` is 3.
# - The report and the log.md entry open with "Since last run" (issue #1083): the actions
#   closed since the previous run, each with its commit (a done action in the committed
#   actions.json whose completed_sha is on HEAD's history and not an ancestor of the previous
#   run's measured commit),
#   the gates gained or lost, and the next blocker; a first run says it is the first run.
# - /assess proposes removals (issue #1084): an instruction file whose always-on text
#   `instruction_placement` flags (over 200 lines and over half the file, the scan's own
#   thresholds) gets a `remove` gap action with its measured always-on line count. It is
#   advice only; the number of practices is never scored.
# - The pruning ledger (issue #1084): "since last run" credits a done `remove` action as
#   "Pruned: always-on text in <file>, saved N always-on lines" (the measured drop) once the
#   file has stayed unflagged and no gate lost for two runs on new commits; a flag or a
#   lost gate resets the count. Nothing is scored.
# - Each skill folder and each plugin `agents/` and `commands/` folder is a unit (issue
#   #1098), with its own readings, Step and place in the unit roll-up. A repository without
#   `SKILL.md` files or a `.claude-plugin/plugin.json` finds the same units as before.
# - The report opens with a three-line executive summary and one call to action (issue
#   #1102): "Step N of M: <what an agent can do here>.", "Next: <the blocking gate as a
#   task>. Then you reach Step N+1: <what it allows>.", "Since last run: ..." (or "First
#   run." / "not known"), then "Start here:" with the gate's task linked to its catalog
#   entry. Plain words: no layer, no condition id and no count of practices.
# - issues #1124 and #1126, Gradle option values, build and project-path tasks.
#   A Gradle option that takes a separate value (--warning-mode all, --console plain,
#   --max-workers 2, -I init.gradle, -F off, -M sha256 and the rest of Gradle 9.4's list,
#   plus -b, -c and the --tests filter) no longer has its value read as the task, so
#   ./gradlew --warning-mode all check credits Layer 5 where it read false. Every task a
#   Gradle line names counts (./gradlew clean check), not only the first.
#   ./gradlew build reads as a check where a tracked build file applies a JVM or Android
#   plugin (java, java-library, application, Kotlin JVM, Android), and null (check_target)
#   where none is read; build -x check, -m build and --dry-run build read false.
#   In the coverage gate, a project-path task (./gradlew :app:jacocoTestCoverageVerification,
#   :app:check) credits only a gate in that project's build file (app/build.gradle[.kts]);
#   a gate in another project's or the root build file reads null (indirect_run). The project
#   path is read under the Gradle root, the nearest tracked settings.gradle[.kts] at or above
#   the -p start or the gate (cd android && ./gradlew :app:... credits android/app/build.gradle);
#   -b / -c read null.
# - `assess_core.py` takes `--profile NAME` or `--levels FILE` (issue #1150): the `level` and
#   `headline` blocks evaluate that maturity model instead of the default. Two profiles ship
#   under `levels/profiles/`: `handover` (every gate plus linters in CI, review evidence and
#   documentation freshness at level 1) and `citizen` (instructions and CI running the
#   tests); each row is a default row, unchanged. Under a profile or a custom model the
#   executive summary opens with the model's name and "Ready for handover (profile:
#   handover)" or the practices still missing, and marks any practice the default model does
#   not define. The Step ladder and the evidence waffle draw the run's model (neither is
#   drawn for a custom file), the since-last-run section compares no gate across two models
#   ("Model changed since last run (default → handover); gates not compared"), and the unit
#   roll-up line says it reads the default model. A default run is unchanged.
#   `/fleet --profile NAME` passes the profile's file as `--levels`.
# - ci_checks reads the toolkit's own regression gate include as running no project tests
#   (issue #1152): a `remote:` of exactly
#   `https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/<ref>/templates/assess-gate.yml`,
#   or a `component:` ending `/ai-native-toolkit/assess-gate@<version>`, is the include status
#   `toolkit_gate` and counts as read, so a GitLab project whose only include is the gate reads
#   `test_or_lint_configured` false when no job runs a check, where it read null. Every other
#   `remote:` include stays `not_offline`.
# - issue #1153: under the opt-in `--managed-settings` (`assess_core.py`, `assess_signals.py`,
#   `fleet.py`) or a single repository's `[agent_ops] managed_settings = true`,
#   `agent_ops.summary.permissions_encoded` also reads true when a managed settings source on
#   the machine running the check (managed-settings.json, managed-settings.d/, the macOS
#   com.anthropic.claudecode plist, the Windows Policies\ClaudeCode registry value) lists a
#   permission entry, and null (not measured, `managed_settings_unreadable`) when the
#   repository has none and a managed source exists but cannot be read. Without the option the
#   reading is unchanged: the repository alone.
# - A monorepo run with two or more units adds a "Learn from:" line to the unit roll-up for
#   each unit whose next gate a sibling unit already holds (issue #1155), naming up to three
#   such siblings. Counts-only output drops the lines. The Steps and the headline are unchanged.
# - A run whose level result is unavailable leaves `prior-level.json` untouched (issue #1158),
#   where it used to delete it: the previous good run's baseline and pruning ledger survive,
#   and "since last run" says the baseline was kept, naming that run.
# - issues #1162 and #1163, a Gradle run's working directory selects the project it runs in.
#   In the coverage gate, an unqualified task run from a known directory (-p app,
#   --project-dir app, cd app && ../gradlew ..., or the step's working-directory: app)
#   credits only a gate at or under app (app/build.gradle, app/sub/build.gradle). A gate in
#   another project (lib/build.gradle) reads null (indirect_run) where it read true, since
#   allprojects {} or a projectDir override can still reach it. A -p value that is a shell
#   expansion, an absolute path or above the root, or a -p in a directory that is not
#   resolved (a defaults: working-directory), also reads null. With no -p and no
#   cd, at the repository root, the reading is unchanged.
#   The Gradle root is now the nearest tracked settings.gradle[.kts] at or above that
#   working directory, falling back to the gate's own directory only when the directory is
#   not resolved. In a repository with two Gradle builds (a root settings.gradle and
#   android/settings.gradle), ./gradlew :app:jacocoTestCoverageVerification at the root no
#   longer credits a gate only in android/app/build.gradle: it reads null (indirect_run).
#   cd android && ./gradlew :app:... and ./gradlew -p android :app:... keep their credit.
#   The working directory is left unresolved (today's reading) where a cd sits on a line
#   that runs Gradle twice, or after a quote (bash -c "cd app && ../gradlew ...").
# - issue #1164, a GitHub step whose command is an expression or a shell variable
#   (run: ${{ inputs.cmd }} in a reusable workflow, ${{ env.CMD }}, $CMD, ${CMD}) reads
#   check_target (null) on test_or_lint_configured, enforced_check_runs and lint_runs where it
#   read no_check_command (false), unless another line settles them; masked, it reads false.
#   A literal env: value of the step, job or workflow is read in its place, so env CMD set to
#   ruff check . with run: $CMD is credited. A variable only as an argument (pytest $ARGS) or
#   part of a word ($GITHUB_WORKSPACE/ci.sh) reads as before. A shell $NAME is not read
#   from env: when the run value assigns NAME itself, when an earlier step writes NAME to
#   $GITHUB_ENV (either form), or when its value holds ;&|()<> (word splitting runs no
#   operator). A composite action's env: values
#   take the caller's literal with: inputs too.
# - issue #1165, a job- or step-level continue-on-error given as an expression reads
#   check_may_pass (null) for the checks it covers where it read enforced_check (true).
#   ${{ matrix.<key> }} is resolved from a literal matrix: on a step copied per value each copy
#   takes its own value; on the job, or a step not copied, any true value masks (not_enforced)
#   and all false keeps the credit. ${{ true }} and ${{ false }} read as the literals.
# - The pruning ledger credits a file trimmed in several removal rounds once, for the whole
#   drop from the first round's starting size (issue #1168), where it credited each round from
#   its own starting size and double-counted the overlap.
# - a new `secrets-guard` row (issue #1173, tag `governance`): level 1, a secrets hook or CI
#   scan (graded config_only, listed, not counted); level 2, a CI scan an agent cannot skip
#   (graded measured, counted), not measured (`secret_scanning_not_read`) on a GitHub
#   repository with no CI scan. It gates no Step.
# - The finding actions read in plain English (issue #1174): "investigate the seam" reads
#   "check why these files change together", and so on for nine of the eleven actions. An
#   action matched to an earlier run only by its directive text (no `finding`) starts again as
#   pending once; one with a `finding` and files keeps its status.
# - A `--profile` run keeps its own committed since-last-run baseline in `prior-level.json`
#   (issue #1184) and compares gates on its own Steps, so `citizen` and `handover` show gates
#   gained and lost on a fresh clone or in CI. A profile run writes only its own record; a
#   default run keeps every profile record; a custom `--levels` run keeps none. The CI gate's
#   `fail_on_gate_loss` reads the run's own record, so a profile run with no record yet reads
#   "no baseline yet" instead of failing.
# - Manifest text reads in plain English (issue #1186): 33 condition fixes, reason-code
#   meanings and next steps, and scan summaries are split into sentences of 25 words or fewer,
#   and the plain-English lint now runs over every manifest prose field except the whys. The
#   whys are unchanged; shorter versions await the maintainer's approval.
# 39 (4.0.0 release): two rule changes, without a bump.
# - issue #922, three hollow cases read unmet. A Makefile target with no prerequisite whose
#   recipe runs only true, :, exit 0, or echo or printf lines with nothing chained after
#   them (no ;, &&, ||, | or >) resolves unknown ("runs nothing"), so a named make test with
#   recipe true no longer counts in agent_environment.check.named_total, and the target is
#   not a check entry point. A settings hook event counts in agent_ops hook_events only when
#   it holds a prompt, agent, http or mcp_tool hook, or a command other than true, : or exit 0; an empty
#   event list or a hook with no type counts 0. Architecture-doc references count toward
#   structure_drift boundary_patterns only when the docs name two or more distinct module
#   paths, compared normalised (./src/a.py is src/a.py, src/core/ is src/core); CODEOWNERS is unchanged. The trade-off: a doc that names several components, of
#   which only one is a module path (#874), now counts 0. In the local corpus,
#   santifer/career-ops moved this way: its docs/ARCHITECTURE.md names dashboard/ beside
#   root scripts and data directories.
# - a new counted condition, `model-ci-gated.1` (issue #1242, tag `governance`): no CI job that
#   calls a model API (an eval harness, an agent action or the Claude Code CLI, a model-provider
#   secret, `pytest -m eval` or an `evals/` path) runs on every default-branch push or every pull
#   request update without a gate (a paths filter, a schedule or manual trigger only, a label,
#   pull request types without synchronize, GitLab `rules:changes:`). `cancel-in-progress` is
#   recorded, not a gate. Not measured (`no_model_jobs`) with no such job, and
#   (`trigger_unresolved`, `ci_jobs_unread`) when none is ungated but one could not be settled.
#   It gates no Step.
ASSESS_METHOD_VERSION = 39
