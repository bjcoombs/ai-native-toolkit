"""Does the repository's CI run a test or lint command? (issue #698)

Layer 5 level 1 of the default maturity model reads this block, so the CI row
measures CI rather than the instruction file Layer 0 already scores. The
evidence is static, read from the files at HEAD:

- The CI configuration files ``lib.command_index`` indexes: GitHub Actions
  workflows (``.github/workflows/*.yml|yaml``) and ``.gitlab-ci.yml``. Other CI
  systems are not parsed (issues #787, #928): ``unread_ci_files`` counts the tracked
  files of the ones this scan knows (``lib.unread_ci``: Jenkins, CircleCI, Azure
  Pipelines in every default layout, Bitbucket, Travis CI, Buildkite, LUCI
  ``.ci.yaml`` and ``infra/config/``, Prow, Zuul, Drone, Woodpecker, Cirrus CI,
  AppVeyor, Semaphore, Cloud Build, Bitrise and Codemagic). When no
  GitHub or GitLab line runs a check and no included jobs are pending, such a
  file makes ``basis`` (and ``enforced_basis`` and ``lint_basis``) ``unread_ci_system``
  and ``enforced_check_runs`` and ``lint_runs`` null: the scanner did not see that system's checks,
  it did not find them absent. ``test_or_lint_configured`` keeps its meaning and stays
  false there. An empty ``Jenkinsfile`` reads the same (accepted: the scan
  cannot tell an empty pipeline from one it cannot parse).
- Only workflow files directly under ``.github/workflows/`` are read: GitHub
  does not run one in a subfolder (#919).
- Each line with its ``- `` / ``run:`` / ``script:`` lead dropped
  (``command_index.ci_line_text``), split into shell segments and tokenised the
  way instruction-file commands are (``command_resolver.extract_commands``), each
  segment read from the command a compound-command lead runs (``if ! pytest``, ``{
  pytest``, ``f() { pytest``, ``(pytest)``; ``lib.ci_compound``, #1061), and classified with ``check_command.is_check_command``: any test or
  check target of a modelled runner counts (``pytest``, ``make check``,
  ``npm test``, ``uv run ruff check``, ``./scripts/test.sh``), and so do the
  common test runners (``dotnet test``, ``swift test``, ``mix test``,
  ``deno test``, ``vendor/bin/phpunit``, ``ctest``, ``bazel test``,
  ``turbo run test``, ``nx affected -t test``, ``task test``, ``hatch test``)
  and a wrapper read by the program it runs (``docker compose run app pytest``,
  ``bundle exec rspec``, ``pnpm exec vitest run``, ``hatch run test``; #920).
  A command that only asks for a version, usage or a test list (``ruff
  --version``, ``dotnet test --list-tests``, ``pytest --co``;
  ``check_command.is_info_invocation``, #919) runs no check, so it counts in no
  signal.
- ``is_check_command`` knows test and check targets but not lint ones
  (``make lint`` is not a check it credits in an instruction file), so a CI
  line also counts when ``check_command.is_lint_command`` credits it: a linter
  run as the program (``ruff``, ``eslint``, ``mypy``, ``uv run flake8``), or a
  lint target of a runner or script (``make lint``, ``npm run lint``,
  ``go vet``, ``cargo clippy``, ``./scripts/lint.sh``). An install that names a
  linter (``npm install eslint``, ``uv sync --extra lint``) never counts, nor
  does a bare list item that only names a linter (``- mypy`` under
  ``matrix:``, ``needs:`` or ``stages:``); ``- ruff check .`` does.
- A bare list item that only names an always-check program (``- pytest``,
  ``- tox``, ``- py.test``, ``- nox``) counts only when its parent key is a
  GitLab ``script:``, ``before_script:`` or ``after_script:``; under
  ``needs:``, a ``matrix:`` axis or any other key it is a value, not a run
  (issue #725). ``- pytest -q`` counts wherever it sits.
- Known limit of the line-based scan (no YAML parser): every line is a
  candidate, so a command inside a non-script value, such as an ``env:`` block
  scalar holding ``pytest``, counts as a run.
- Each line is classified on its own, so an apostrophe in a step name or a
  fenced block inside a script cannot swallow the lines after it. A command
  continued with ``\\`` is first joined onto its first line (``lib.ci_continuations``,
  #799), so ``mvn ${MVN_ARGS} \\`` / ``verify`` reads ``verify``; a Maven run with its
  tests skipped and only lifecycle phases (``mvn -DskipTests verify``), or a Gradle run
  with ``test`` excluded and no other test task (``./gradlew check -x test``, #1046), reads
  as a target it cannot settle (``check_target``, in ``basis`` too: null), never credit.
  ``./gradlew build`` is a check where a JVM or Android plugin is applied, else null (#1124).
  A Gradle dry run (``./gradlew -qm check``, ``--dry-run``, ``--task-graph``) runs nothing,
  for Layer 5 and Layer 3 alike, and ``pdm run`` / ``poe`` are read through to the program
  or script they run (``lib.runner_args``).
- A ``run`` / ``script`` / ``command`` input under a ``uses:`` step's ``with:`` (#799,
  ``lib.ci_action_inputs``) reads as shell only for an action that runs it: a local action
  whose committed ``action.yml`` runs ``${{ inputs.<name> }}``, or a listed runner.
  ``actions/github-script`` is not read; any other action's input counts in
  ``test_or_lint_configured`` and reads ``check_target`` (null) for the enforced signals.
- A step ``uses:`` of a local composite action (#923, ``lib.ci_composite``) has the
  action's ``run:`` steps copied in after it, one level and repo-local only, with the
  caller's ``continue-on-error`` and ``if:`` on each copy and its literal ``with:`` values
  substituted for ``${{ inputs.<name> }}`` (a nested local action, or an input left in
  command position, reads unknown as below); a step whose command is a
  ``${{ matrix.<key> }}`` expression is copied once per literal matrix value
  (``lib.ci_matrix``, ``include`` / ``exclude`` applied), and a block-list axis it
  consumes is not read again. A matrix command that cannot be expanded reads as a
  command it cannot settle: ``check_target`` (null) on all three signals when nothing
  else settles them, never false unless masked.
- A command that is an expression or a shell variable (``${{ inputs.cmd }}``, ``$CMD``;
  #1164) reads the same way unless a literal ``env:`` value settles it, and a
  ``continue-on-error`` expression (#1165) that a literal matrix does not settle reads its
  checks as ``check_may_pass`` (null), never enforced (``lib.ci_expressions``).
- Triggers are not read: a check in a workflow that runs only on push counts
  the same as one on pull requests.

GitLab includes are resolved offline where the included file is on disk
(``lib/ci_includes.py``, #766): ``local:`` files of the checkout, and
``project:`` / ``component:`` files of a clone in the ghsync root passed as
``ghsync_root`` (``assess_signals --ghsync-root``; ``fleet.py`` passes its
ROOT). Each resolved file's lines are classified the same way and counted in
``included_check_commands``. ``includes`` lists one record per include item
(kind, id, status, ref_basis, depth; never a file name or text), with the
``resolved_includes`` / ``unresolved_includes`` counts. The toolkit's own regression gate
template (status ``toolkit_gate``, ``ci_includes.SETTLED``, #1152) counts as resolved: its
one job runs no project tests.

Under ``--platform`` (and in a full ``/assess`` run) a repository whose
includes did not all resolve offline also gets GitLab's merged configuration
from the CI lint API (``lib/hosting/gitlab_ci_lint.py``, #796). Its check lines are
counted in ``merged_check_commands``, apart from the offline counts (the merged
file repeats the own and resolved jobs). ``merged_config`` says what happened:
``not_read`` (offline), ``not_needed`` (every include resolved offline: the
offline reading wins), ``read``, ``invalid`` or ``unavailable``, with the
reason code in ``merged_config_reason``. The merged text is never published.
The merged configuration can lack jobs: the lint API merges in its own
context, so an include behind ``rules:`` may be left out, and a child or
downstream pipeline (a ``trigger:`` job) is not expanded. A merged
configuration with no check therefore reads false only when neither shows
(``merged_partial``: a ``rules:`` key in the ``include:`` section of the
repository's file or of a template read offline, or a ``trigger:`` job in any
of those or in the merged file); otherwise the signal stays null with
``basis: merged_config_partial``. Unknown beats wrong. A ``rules:`` include
inside a template neither the clone nor the merge shows is not seen.

``test_or_lint_configured`` is true when any CI line, own or included, runs a check.
With none it is false, except when a CI file pulls jobs from a file this run
could not read (a GitLab include that did not resolve, a GitHub job-level
``uses:`` calling another repository's reusable workflow;
``shared_tooling.pulls_jobs_from_elsewhere``): the check may live there, so the
signal is null (unknown), never false. A GitLab file whose every include
resolved and holds no check reads false, and so does one whose merged
configuration was read in full and holds none. ``basis`` names which case applied:
``merged_config`` when only the merged configuration found the check.

``enforced_check_runs`` (issue #775) is the same test with masked checks left
out: a check that runs but cannot fail its job (``pytest || true``,
``continue-on-error: true``, ``allow_failure: true``, ``set +e``, an exit-zero
flag such as ``--exit-zero``, a later ``exit 0`` in a shell without errexit, a literal
``if: false``; the rules and
their known limits are in ``lib.ci_masking``), and with checks that do not
run on ordinary changes left out too (a ``workflow_dispatch``- or
``schedule``-only workflow, a GitLab ``when: manual`` job; ``lib.ci_triggers``).
``enforced_basis`` says which case applied:

- ``enforced_check`` (true): an own or included line runs an unmasked check on
  ordinary changes (GitLab's merged configuration is read the same way when the
  files alone hold none).
- ``included_jobs`` (null): none does, and a CI file pulls jobs this run could
  not read.
- ``unknown_trigger`` (null): none does, but an unmasked check sits in a
  workflow or job whose triggers this reader cannot settle.
- ``check_may_pass`` (null, #1008): none does, but a line on ordinary changes runs a
  check whose failure the step may hide (``lib.ci_unsure``: ``pytest || echo failed``,
  ``if pytest; then ...; fi``, ``until pytest; do break; done``, ``pytest && echo ok``
  with more of the step after it); ``lint_basis`` alike.
- ``script_not_found`` (null, #937): none does, but one runs a check through a
  committed script the tree does not hold (``lib.ci_scripts``: ``./ci.sh`` with no
  ``ci.sh``); ``lint_basis`` alike. ``test_or_lint_configured`` still counts it. A committed
  script whose body provably fails on a failing check is one (#1033, ``ci_scripts.ScriptTree``).
- ``check_target`` (null): none does, but an unmasked line on ordinary changes
  runs a target whose recipe is not read (#920, ``check_command.may_check``):
  ``make foo``, ``task foo``, a bare ``make``, ``hatch run foo``, ``npm run foo``,
  ``./scripts/foo.sh``, ``docker run img``. A setup-named one (``make install``,
  ``npm run build``, ``./scripts/deploy.sh``) is not; masked or off-trigger, it reads false.
- ``unread_ci_system`` (null): none does, and the tree holds a CI file of a
  system this scan does not read (#787). A masked or off-trigger check beside it
  still reads null: the unread system may run the check unmasked.
- ``external_ci`` (null, ``--platform`` only, #928): none does, and more than
  half of the sampled merged pull requests carry a check run or commit status of
  a known external CI system (``lib.external_ci``). It replaces the false cases
  below and never gives credit.
- ``external_ci_marker`` (null, #928): none does, the tree tracks no CI file of
  any kind (no workflow, no ``.gitlab-ci.yml``, no unread CI file) and the root
  holds a Prow ``OWNERS_ALIASES`` file. An ``OWNERS`` file alone is not a marker.
  ``external_ci`` from platform evidence comes first.
- ``not_enforced`` (false): checks run, but each is masked or off the ordinary
  triggers.
- ``no_check_command`` / ``no_ci`` (false): as ``basis``.

``lint_runs`` (issue #782) is Layer 3 level 1: does CI run a linter? It reads
the same lines with the same masking and trigger rules, but only lint lines
count (``check_command.is_lint_run``: ``is_lint_command`` without
``--version`` / ``--help`` / ``-V``, plus a go-task lint target), and so does
a GitHub ``uses:`` step of a known lint action (``LINT_ACTIONS``:
golangci-lint-action, ruff-action, super-linter, ``reviewdog/action-*`` apart
from action-setup and action-suggester, lint-action, markdownlint-cli2-action;
an unknown third-party action is not read), masked by ``continue-on-error`` and
kept off by triggers like any step. A step whose literal ``with:`` input makes it
install only (``ruff-action`` ``args: --version``, ``golangci-lint-action`` v9+
``install-only: true``; ``lib.ci_lint_inputs``, #893) is no lint run, and one whose
deciding input is not a literal it reads (a ``${{ }}`` expression) reads as a step that
may lint: ``check_target`` (null) unless another line settles it. A lone ``- flake8`` item counts under a
GitLab ``script:`` key and nowhere else. ``pre-commit/action`` runs
``pre-commit run --all-files`` and reads as that line does (below). A check
target or script may run a linter inside it, and the line alone cannot show
it (``check_command.may_lint``: ``make check``, ``npm test``, ``tox``,
``./scripts/test.sh``, ``pre-commit run``; recipes are not read), so with no
lint line such a check reads null. A direct test-program call (``pytest``,
``go test``, ``cargo test``, ``npx jest``) runs no linter, and a target whose recipe
is not read (``check_command.may_check``: ``make foo``, ``npm run foo``,
``./scripts/foo.sh``) reads null here as it does at Layer 5 (#926). The lint and
analyser commands of other ecosystems come from ``lib.lint_vocabulary`` (#926):
``phpstan``, ``mix credo``, ``./gradlew ktlintCheck``, ``dart analyze``,
``swiftlint``, ``clang-tidy``, and a formatter in its check mode. A formatter that
rewrites files (``clang-format -i``, ``mix format``) counts only when a later
unmasked line of its job runs ``git diff --exit-code`` (``lib.ci_lint_gates``).
``lint_basis``:

- ``lint_command`` (true): an own, included or merged line runs an unmasked
  linter on ordinary changes.
- ``included_jobs`` / ``merged_config_partial`` (null): as ``basis``.
- ``unknown_trigger`` (null): an unmasked lint line's triggers are not settled.
- ``check_may_pass`` (null): a lint line's failure may not fail its job (#1008).
- ``script_not_found`` (null): the only lint line runs a script no tracked file matches.
- ``check_target`` (null): no lint line, but an unmasked check target or
  script may run one.
- ``unread_ci_system``, ``external_ci``, ``external_ci_marker`` (null): as
  ``enforced_basis``.
- ``analysers_in_build`` (null, #926): no lint line, but a CI ``dotnet build`` /
  ``msbuild`` line runs the analysers a committed ``Directory.Build.props`` or
  ``*.csproj`` turns on (``EnforceCodeStyleInBuild``, ``AnalysisLevel``); their
  severities live in ``.editorconfig``, which is not evaluated.
- ``compiler_gate_only`` (null, #926): no lint line, and the only static gate is a
  compiler run with warnings as errors (``mix compile --warnings-as-errors``,
  ``-Werror``, ``dotnet build -warnaserror``, a CI .NET build with
  ``TreatWarningsAsErrors`` committed). ``analysers_in_build`` wins when both apply.
  Both sit after ``unread_ci_system`` and the external codes, and apply only where no lint
  line runs at all:
  a masked or off-trigger linter keeps ``not_enforced``.
- ``not_enforced`` (false): lint lines run, but each is masked or off the
  ordinary triggers (a masked compiler gate reads this too).
- ``no_lint_command`` / ``no_ci`` (false): CI files with no lint or
  may-lint line, or no CI file.

``test_or_lint_configured`` and ``enforced_check_runs`` keep their meaning, apart
from #919: a version-only command and a subfolder workflow count in no signal.

``masked_check_commands`` counts the masked check lines and
``off_trigger_check_commands`` the unmasked ones that do not run on ordinary
changes, so a reader sees why the two signals disagree. ``basis`` describes
``test_or_lint_configured``; it reads ``external_ci`` or ``external_ci_marker`` where
``enforced_basis`` does, and ``test_or_lint_configured`` keeps its value (as for
``unread_ci_system``). Layer 5 level 1 reads ``enforced_check_runs``; a stored
scan without the key reads unknown there.

Does CI run a test or lint command (issue #698), can it fail the job (#775), and does
it run a linter (#782)?
Layer 5 level 1 of the default maturity model reads it, so the CI row measures CI, not
the instruction file; Layer 3 level 1 reads `lint_runs`.
`scan_ci_checks(repo_root, ghsync_root, merged_ci)` reads the files `command_index.ci_config_files`
selects (GitHub Actions workflows directly under `.github/workflows/`, never a subfolder,
which GitHub does not run (#919); `.gitlab-ci.yml`) plus the GitLab include files
`ci_includes.resolve_includes` read (#766), and writes `{available,
ci_files, check_commands, included_files, included_check_commands, resolved_includes,
unresolved_includes, includes[], test_or_lint_configured, basis, enforced_check_runs,
enforced_basis, masked_check_commands, off_trigger_check_commands, lint_runs,
lint_basis, unread_ci_files}`. Each line (`\\` continuations joined first and `with:`
inputs settled, #799: see above), with its `- ` / `run:` / `script:` lead and a trailing `\\` dropped
(`command_index.ci_line_text`), is split into shell segments by
`command_resolver.extract_commands` on its own (an apostrophe or fence on one line
cannot swallow the next); `check_commands` counts the lines with a segment that
`check_command.is_check_command` or `is_lint_command` credits; a bare list item
that only names a linter (`- mypy` under `matrix:`) does not count, and one that only
names an always-check program (`- pytest`, `- tox`) counts only when its parent key is
a GitLab `script:` / `before_script:` / `after_script:` (#725: under `needs:` or a
`matrix:` axis it is a value). `count_check_lines(lines, context=)` is the same count over a
line list, reading parent keys from `context` (the original lines) when given. Triggers are
not read. Known limit: lines are read without a YAML parser, so a command inside a
non-script value (an `env:` block scalar holding `pytest`) counts as a run. `test_or_lint_configured` is true with any check
line, own or included (`included_check_commands` counts the included ones); with none it
is null (`basis: included_jobs`) when a CI file pulls jobs this run could not read
(`shared_tooling.pulls_jobs_from_elsewhere`: a GitLab `include:` with any unresolved
item, a job-level `uses:` of another repository's reusable workflow), else
false (`no_check_command`, `no_ci` with no CI file, or `unread_ci_system`, below): a `.gitlab-ci.yml` whose
every include resolved and holds no check reads false. Its third argument is the
`gitlab_merged_ci` reading (`merged_ci.MergedCi`, #796): where an include did not resolve
offline, `fleet --platform` (and a full `/assess` run) asks GitLab's CI lint API for the
merged configuration, whose check lines are counted apart in `merged_check_commands`
(the merged file repeats the own and resolved jobs). A check there makes the signal true
(`basis: merged_config`, used only when no own or included line ran one: offline wins); a
merged configuration read with none makes it false, unless it may lack jobs
(`merged_partial`): a `rules:` key in the `include:` section of the repository's file or of a
template read offline (`conditional_include`; the lint API may leave that include out), or a
`trigger:` job in any of those or in the merged file (`child_pipeline`; a child or downstream
pipeline is not expanded). Then the signal stays null with `basis: merged_config_partial`:
unknown beats wrong. `merged_config` is `not_read`
(offline), `not_needed`, `read`, `invalid` or `unavailable` (reason code in
`merged_config_reason`); the merged text is never in the block. Known limit: a `rules:` include inside a
template that neither a clone nor the merge shows is not seen. With no GitLab CI file, or one with no `include:`, `merged_config` is
`not_read`. The module itself is
offline: it imports the value type, never a platform client.
`enforced_check_runs` and `masked_check_commands` (issue #775) apply the same
test to the lines `ci_masking.unmasked_lines` returns:
`enforced_check_commands(text) -> (unmasked, masked)` counts the check lines that
can fail their job and the rest (`masked = check_commands - unmasked`), and
`enforced_check_runs` is true with any unmasked check line (own, included, or
in the merged configuration when those hold none)
whose workflow or job runs on ordinary changes (`ci_triggers`; the unmasked lines
off those triggers are `off_trigger_check_commands`, and an unsettled trigger reads
null with `enforced_basis: unknown_trigger`), else null under the
same `included_jobs` rule, else null with `enforced_basis: check_target` when an
unmasked line on ordinary changes runs a target whose recipe is not read
(`_line_may_check`: `check_command.may_check`, issue #920), else false. Layer 5 level 1 reads
`enforced_check_runs`; a stored scan without the key reads unknown there. `basis`
still describes `test_or_lint_configured`, which keeps its meaning apart from #919's
version-only and subfolder cases, which count nowhere.
`lint_runs` (issue #782) is the enforced test for lint lines alone: the same masking
and trigger rules, the same own, included and merged files, with
`count_check_lines(..., test=)` and `_trigger_tally(text, workflow, tests)` taking a
per-line predicate (`_line_runs_lint`: `check_command.is_lint_run`, never a bare `-
mypy` item, or a `uses:` step of a known lint action, `lint_action(line)` over
`LINT_ACTIONS` plus `reviewdog/action-*` apart from `action-setup` and
`action-suggester`: golangci-lint-action, ruff-action, super-linter, lint-action,
markdownlint-cli2-action; an unknown third-party action is not read, and
`continue-on-error` and triggers apply as to any step; a lone `- flake8` item counts
under a GitLab `script:` key only, `_script_items` reading its parent;
`_line_may_lint`: `check_command.may_lint`, or a `pre-commit/action` step, which runs
`pre-commit run --all-files` and so reads as that line does). `lint_basis` is
`lint_command` (true: an unmasked lint line on ordinary changes), `included_jobs` /
`merged_config_partial` (null, as `basis`), `unknown_trigger` (null: a lint line's
triggers are not settled), `check_target` (null: no lint line, but an unmasked check
target or script that may lint inside it; recipes are not read), `not_enforced`
(false: every lint line is masked or off the ordinary triggers), `no_lint_command` or
`no_ci` (false). Layer 3 level 1 reads `lint_runs`, so a suppression comment only
caps the row; a stored scan without the key reads unknown there.
CI systems the scan does not parse (issues #787, #928): `unread_ci_files` counts the
tracked files at the unread-CI paths of `unread_ci.py` (listed there). With no own, included or merged check line and no pending
included jobs, one makes `basis` `unread_ci_system` (`test_or_lint_configured` stays false),
and with no enforced check and no `included_jobs` / `unknown_trigger` case, it makes
`enforced_check_runs` null with `enforced_basis: unread_ci_system` (a masked check
beside it still reads null). With no lint line on ordinary triggers and no check target,
one also makes `lint_runs` null with `lint_basis: unread_ci_system` (Layer 3 level 1
unknown). Their commands are not read; an empty `Jenkinsfile` reads the same.
Off-repository CI (issue #928), after `unread_ci_system` and before the false cases:
`external_ci` when the private `external_ci` reading (`external_ci.py`, `--platform`
only) applies, else `external_ci_marker` when the tree tracks no CI file of any kind
(no workflow, no `.gitlab-ci.yml`, no unread CI file) and the root holds a Prow
`OWNERS_ALIASES` file. Either makes `enforced_check_runs` and `lint_runs` null with that
basis in place of `not_enforced`, `no_check_command`, `no_lint_command` or `no_ci`, and
`basis` reads it with `test_or_lint_configured` still false. Neither gives credit: a read
enforced check still reads true. An `OWNERS` file alone, a nested `OWNERS_ALIASES` and an
environment-variable read (`ENV["BUILDKITE"]`, `LUCI_`) are not markers.
Issue #926: a target whose recipe is not read (`_line_may_check`: `make foo`, `npm run
foo`, `./scripts/foo.sh`; any `ci` name, #951: see `lib.named_check`) reads `lint_basis` `check_target` too, as at Layer 5. A rewrite
formatter line counts as a lint line when `ci_lint_gates.rewrite_gated` names it
(`count_check_lines(..., extra=)`). With `lint_runs` otherwise false, `ci_lint_gates.lint_gate`
gives the two new null codes, `analysers_in_build` ahead of `compiler_gate_only`, from the
`line_dotnet_build` and `line_compiler_gate` tallies (unmasked, on ordinary or unsettled
triggers), and only where `lint_basis` would be `no_lint_command`: a masked linter beside a
build keeps `not_enforced`. A masked or off-trigger compiler gate alone reads
`not_enforced`. A lone `- msbuild` item reads its parent key like a lone linter
(`_lone_build`), so a `matrix:` value is not a build.
The per-line tests named above (`_line_runs_check`, `_line_runs_lint`, `_line_may_lint`,
`_line_may_check`, `_script_items`, `lint_action`, `count_check_lines`) live in
`lib.ci_lines` (#1061); this module re-exports `count_check_lines`, `lint_action`,
`LINT_ACTIONS` and `MAY_LINT_ACTIONS`.
Issue #893: `lib.ci_lint_inputs.read_lint_inputs` reads each ruff-action and
golangci-lint-action step's `with:` block; `_trigger_tally(lint_inputs=)` drops an
install-only step from the lint count and counts an unsettled one for `_line_may_lint`.
Tests: `tests/test_ci_checks.py` (`test_lint_runs_states` is the #782 fixture table),
`tests/test_ci_lint_inputs.py` (#893),
`tests/test_external_ci.py` (#928), `tests/test_ci_lint_gates.py` (the #926 fixtures),
`tests/test_gitlab_ci_lint.py`, and the Layer 3 and Layer 5 rows in
`tests/test_level_signals.py`.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from lib.ci_action_inputs import Inputs, ReadLocal, read_inputs
from lib.ci_expressions import follow_workflow
from lib.ci_continuations import join_continued
from lib.ci_includes import SETTLED, IncludeRecord, Resolution, resolve_includes
from lib.ci_lint_gates import line_compiler_gate, line_dotnet_build, lint_gate, rewrite_gated
from lib.ci_lint_inputs import NO_INPUTS, LintInputs, read_lint_inputs
from lib.ci_lines import (
    LINT_ACTIONS as LINT_ACTIONS, MAY_LINT_ACTIONS as MAY_LINT_ACTIONS, LineTest,
    _line_may_check, _line_may_lint, _line_runs_check, _line_runs_lint, _skipped_check,
    count_check_lines as count_check_lines, lint_action as lint_action)
from lib.ci_check_state import (
    EnforcedBasis as EnforcedBasis, External as External, LintBasis as LintBasis, Tally,
    _enforced_state, _lint_state, _max, _sum)
from lib.ci_masking import mask_lines, unmasked_lines
from lib.named_check import instruction_words, named_count
from lib.ci_triggers import Trigger, gitlab_line_triggers, keep, resolve_workflows, workflow_trigger
from lib.ci_scripts import ScriptTree, runs_missing
from lib.command_index import CI_CONFIG_FILES, ci_config_files, read_config, repo_files
from lib.dot_source import dot_as_source
from lib.runner_args import reads_pdm_scripts
from lib.external_ci_rules import as_external
from lib.merged_ci import MergedStatus, as_merged
from lib.shared_tooling import include_lines, pulls_jobs_from_elsewhere
from lib.unread_ci import UNREAD_CI_FILES as UNREAD_CI_FILES, unread_ci_paths

# A ``rules:`` key inside the include section, block (``rules:``) or flow (``{..., rules: [..]}``).
_INCLUDE_RULES = re.compile(r"""(?:^|[\s{,-])["']?rules["']?\s*:""")


def conditional_include(text: str) -> bool:
    """True when the top-level ``include:`` section of a GitLab CI file has a ``rules:`` key:
    the lint API merges in its own context and may leave that include out (#796)."""
    return any(_INCLUDE_RULES.search(line.split("#", 1)[0]) for _, line, _ in include_lines(text))


# A job's indented ``trigger:`` key (not a job named trigger, #958): a child or downstream pipeline.
_TRIGGER = re.compile(r"""^[ \t]+["']?trigger["']?\s*:""", re.MULTILINE)


def child_pipeline(text: str) -> bool:
    """True when a GitLab CI file has a job with ``trigger:`` (a child or downstream pipeline)."""
    return _TRIGGER.search(text) is not None


def merged_partial(text: str, resolution: Resolution | None, merged_text: str | None) -> bool:
    """True when GitLab's merged configuration may lack jobs (#796): an include under
    ``rules:`` in the repository's file or a template read offline (the lint API merges in
    its own context and may leave it out), or a ``trigger:`` job in the file, a template or
    the merged file (a child pipeline is not expanded). A check-free merge then stays null."""
    texts = [text, *(f.text for f in (resolution.files if resolution is not None else ()))]
    return (any(conditional_include(t) for t in texts)
            or any(child_pipeline(t) for t in (*texts, merged_text or "")))


CiChecksBasis = Literal["check_command", "merged_config", "included_jobs",
                        "merged_config_partial", "unread_ci_system", "external_ci",
                        "external_ci_marker", "check_target", "no_check_command", "no_ci"]

# The offline Prow marker (#928): a root OWNERS_ALIASES with no CI file of any kind.
PROW_MARKER = "OWNERS_ALIASES"


class CiChecksBlock(TypedDict):
    """``run-context.json`` ``ci_checks``; a degraded block is ``{available: false, reason}``."""

    available: bool
    reason: NotRequired[str]
    ci_files: int
    check_commands: int
    included_files: int
    included_check_commands: int  # check lines in included files read offline (#766)
    resolved_includes: int
    unresolved_includes: int
    includes: list[IncludeRecord]
    merged_config: MergedStatus  # the CI lint API read (#796)
    merged_config_reason: str | None  # its reason code when unavailable
    merged_check_commands: int  # check lines in the merged configuration
    test_or_lint_configured: bool | None
    basis: CiChecksBasis
    enforced_check_runs: bool | None
    enforced_basis: EnforcedBasis
    masked_check_commands: int
    off_trigger_check_commands: int
    lint_runs: bool | None  # Layer 3 level 1 (#782)
    lint_basis: LintBasis
    unread_ci_files: int  # tracked unread CI files (#787, #928; lib.unread_ci)


def check_commands(text: str) -> int:
    """How many lines of one CI file's text run a test or lint check (``\\`` lines joined, #799)."""
    return count_check_lines(join_continued(text).splitlines())


def enforced_check_commands(text: str) -> tuple[int, int]:
    """``(unmasked, masked)``: the check lines of one CI file that can fail their job, and the rest."""
    text = join_continued(text)
    lines = text.splitlines()
    total = count_check_lines(lines)
    enforced = count_check_lines(unmasked_lines(text), context=lines)
    return enforced, max(total - enforced, 0)




def _is_lint_test(test: LineTest) -> bool:
    """``_line_runs_lint``, bare or bound to a ``present`` predicate (#937)."""
    return getattr(test, "func", test) is _line_runs_lint


def _declared_check(line: str, *, script_item: bool = True) -> bool:
    """A declared-only ``with:`` input line (#799) that runs or may run a check or linter."""
    return any(t(line, script_item=script_item) for t in (_line_runs_check, _line_runs_lint, _line_may_check))


def _trigger_tally(text: str, workflow: Trigger | None,
                   tests: Sequence[LineTest] = (_line_runs_check,),
                   declared: frozenset[int] = frozenset(),
                   unknown: frozenset[int] = frozenset(),
                   lint_inputs: LintInputs = NO_INPUTS,
                   may_pass_lines: frozenset[int] = frozenset()) -> list[Tally]:
    """Per line test: ``(ordinary, unknown trigger, off trigger, masked, may pass)`` lines of one CI file.

    ``workflow`` is a GitHub workflow's settled trigger; None reads GitLab job triggers.
    The lint test also counts the rewrite lines a ``git diff`` gate makes lint runs (#926).
    A ``declared`` line (an unread action's ``with:`` input, ``lib.ci_action_inputs``) counts
    for no test but ``_line_may_check``, which counts it when it runs or may run a check (#799).
    An ``unknown`` line (a matrix command not expanded, ``lib.ci_matrix``, #923) counts for
    ``_line_may_check`` whatever it holds, unless masked. A ``lint_inputs`` step
    (``lib.ci_lint_inputs``, #893) is no lint run; an unsure one counts for ``_line_may_lint``.
    A ``may_pass_lines`` line (a job or step under a ``continue-on-error`` expression,
    ``lib.ci_expressions``, #1165) that the masking left unmasked reads as one whose failure
    may not fail the job.
    """
    text = join_continued(text)
    lines = text.splitlines()
    gated = rewrite_gated(text) if any(_is_lint_test(t) for t in tests) else frozenset()
    unmasked, unsure = mask_lines(text)
    for i in may_pass_lines:   # a declared line keeps its own check_target reading
        if i not in declared and unmasked[i].strip():
            unsure[i], unmasked[i] = unmasked[i], ""
    held = [line if i in declared else "" for i, line in enumerate(unmasked)]
    unmasked, unsure = _drop(unmasked, declared), _drop(unsure, declared)
    triggers: list[Trigger] = (gitlab_line_triggers(text) if workflow is None
                               else [workflow] * len(lines))
    kept = [keep(unmasked, triggers, t) for t in ("ordinary", "unknown", "not_ordinary")]
    may = [keep(unsure, triggers, t) for t in ("ordinary", "unknown")]
    held_kept = [keep(held, triggers, t) for t in ("ordinary", "unknown", "not_ordinary")]
    out: list[Tally] = []
    steps = lint_inputs.install_only | lint_inputs.unsure
    for test in tests:
        skip = steps if _is_lint_test(test) else frozenset()
        extra = (gated if _is_lint_test(test) else unknown if test is _line_may_check
                 else lint_inputs.unsure if test is _line_may_lint else frozenset())
        counts = [count_check_lines(_drop(k, skip), context=lines, test=test, extra=extra) for k in kept]
        if test is _line_may_check and declared:
            counts = [c + count_check_lines(h, context=lines, test=_declared_check, extra=unknown)
                      for c, h in zip(counts, held_kept, strict=True)]
        may_pass = sum(count_check_lines(_drop(k, skip), context=lines, test=test) for k in may)
        total = count_check_lines(_drop(lines, declared | skip), context=lines, test=test, extra=extra)
        masked = max(total - sum(counts) - may_pass, 0)
        out.append((counts[0], counts[1], counts[2], masked, may_pass))
    return out


def _drop(lines: Sequence[str], indexes: frozenset[int]) -> list[str]:
    """``lines`` with the lines at ``indexes`` blanked."""
    return [("" if i in indexes else line) for i, line in enumerate(lines)]


def _basis(found: bool, merged: bool, *, elsewhere: bool, partial: bool, unread: bool,
           files: bool, external: External = None, skipped: bool = False) -> tuple[bool | None, CiChecksBasis]:
    """``(test_or_lint_configured, basis)``: an own or included check, the merged one, pending
    included jobs, a Maven or Gradle run with its tests skipped (#799, #1046: it may still run lint
    plugins, so null), an unread CI system (#787: the value stays false, its meaning unchanged)."""
    if found:
        return True, "check_command"
    if merged:
        return True, "merged_config"
    if elsewhere:
        return None, "merged_config_partial" if partial else "included_jobs"
    if skipped:
        return None, "check_target"
    if unread:
        return False, "unread_ci_system"
    if external:
        return False, external
    return False, "no_check_command" if files else "no_ci"


def _local_reader(root: Path, tracked: frozenset[str]) -> ReadLocal:
    """Read a tracked file inside ``root`` for ``ci_action_inputs`` (#799), never one a
    symlink takes outside the checkout."""
    def read(rel: str) -> str | None:
        if rel not in tracked or not (root / rel).resolve().is_relative_to(root):
            return None
        return read_config(root, rel)
    return read


@dot_as_source()   # . ./test.sh reads as source ./test.sh on a CI line (#1033)
@reads_pdm_scripts   # a [tool.pdm.scripts] name is a script, not a program (#1046)
def scan_ci_checks(repo_root: Path, ghsync_root: Path | None = None,
                   merged_ci: object = None, external_ci: object = None) -> CiChecksBlock:
    """Build the ``ci_checks`` block for the repository at ``repo_root``. ``merged_ci`` is the
    ``gitlab_merged_ci`` reading (``lib/hosting/gitlab_ci_lint.py``) and ``external_ci`` the
    ``external_ci`` one (``lib/external_ci.py``); anything else is not read."""
    root = repo_root.resolve()
    merged = as_merged(merged_ci)
    tracked = repo_files(root)
    files = ci_config_files(tracked)
    unread = len(unread_ci_paths(tracked))
    external: External = (
        "external_ci" if as_external(external_ci).applies
        else "external_ci_marker" if PROW_MARKER in tracked and not files and not unread
        else None)
    checks = 0
    included = 0
    elsewhere = 0  # files whose pulled jobs this run cannot read
    partial_merge = False  # the merged configuration may lack jobs (a rules: include, a child pipeline)
    resolution = resolve_includes(root, ghsync_root) if any(f in CI_CONFIG_FILES for f in files) else None
    reader = _local_reader(root, tracked)
    followed = {rel: follow_workflow(read_config(root, rel), reader)
                for rel in files if rel not in CI_CONFIG_FILES}   # #923, #1164, #1165
    inputs = {rel: (Inputs(read_config(root, rel), frozenset()) if rel in CI_CONFIG_FILES
                    else read_inputs(followed[rel].text, reader)) for rel in files}
    # An unknown line read_inputs blanked (a github-script script:) is not shell: drop it.
    unexpanded = {rel: frozenset(i for i in f.unknown if inputs[rel].text.splitlines()[i].strip())
                  for rel, f in followed.items()}
    texts = {rel: inputs[rel].text for rel in files}
    for rel in files:
        text = texts[rel]
        checks += check_commands(text)
        gitlab = rel in CI_CONFIG_FILES
        pulls = pulls_jobs_from_elsewhere(text, gitlab=gitlab)
        included += pulls
        # Fully read only when the resolver saw the includes (a record each) and read them
        # all: a symlinked .gitlab-ci.yml it refuses stays unknown, never false.
        read_all = resolution is not None and bool(resolution.includes) and resolution.unresolved == 0
        if gitlab and merged.read and merged_partial(text, resolution, merged.text):
            partial_merge = True
        merged_all = gitlab and merged.read and not partial_merge
        if pulls and not (gitlab and (read_all or merged_all)):
            elsewhere += 1
    included_checks = sum(check_commands(f.text) for f in resolution.files) if resolution else 0
    merged_checks = check_commands(merged.text) if merged.read and merged.text is not None else 0
    records = list(resolution.includes) if resolution else []
    workflows = resolve_workflows(
        {rel: workflow_trigger(texts[rel]) for rel in files if rel not in CI_CONFIG_FILES}, texts)
    present = ScriptTree(root, tracked)
    tests: tuple[LineTest, ...] = tuple(
        partial(t, present=present) for t in (_line_runs_check, _line_runs_lint,
                                              instruction_words(runs_missing(_line_runs_check)),
                                              runs_missing(_line_runs_lint))
    ) + (_line_may_lint, _line_may_check, line_compiler_gate, line_dotnet_build)
    tallies = [_trigger_tally(texts[rel], workflows.get(rel), tests, inputs[rel].declared,
                              unexpanded.get(rel, frozenset()),
                              NO_INPUTS if rel in CI_CONFIG_FILES else read_lint_inputs(texts[rel]),
                              followed[rel].may_pass if rel in followed else frozenset())
               for rel in files]
    tallies += [_trigger_tally(f.text, None, tests) for f in resolution.files] if resolution else []
    check_t, lint_t, miss_t, lint_miss_t, may_t, may_check_t, gate_t, dotnet_t = (
        _sum(t[i] for t in tallies) for i in range(len(tests)))
    ordinary, unknown, off, masked, may_pass = check_t
    if merged.read and merged.text is not None:
        # GitLab's merged configuration (#796) repeats the file's own jobs: read it only
        # where the files alone found no enforced check, as basis reads merged_config.
        m_check, m_lint, m_miss, m_lint_miss, m_may, m_may_check, m_gate, m_dotnet = _trigger_tally(
            merged.text, None, tests)
        m_ordinary, m_unknown, m_off, m_masked, m_may_pass = m_check
        if not lint_t[0]:
            lint_t, may_t = _max(lint_t, m_lint), _max(may_t, m_may)
            lint_miss_t = _max(lint_miss_t, m_lint_miss)
            gate_t, dotnet_t = _max(gate_t, m_gate), _max(dotnet_t, m_dotnet)
        if not ordinary:
            ordinary, unknown, may_pass = m_ordinary, max(unknown, m_unknown), max(may_pass, m_may_pass)
            may_check_t, miss_t = _max(may_check_t, m_may_check), _max(miss_t, m_miss)
        if not (checks or included_checks):
            off, masked = m_off, m_masked
    found = bool(checks or included_checks or any(check_t))   # a read script body (#1033)
    runs, basis = _basis(found, bool(merged_checks), elsewhere=bool(elsewhere), partial=partial_merge,
                         unread=bool(unread), files=bool(files), external=external,
                         skipped=any(unexpanded.values()) or any(count_check_lines(join_continued(t).splitlines(), test=_skipped_check)
                                     for t in (*texts.values(), *(f.text for f in (resolution.files if resolution else ())))))
    enforced_runs, enforced_basis = _enforced_state(
        ordinary, unknown, may_check_t, elsewhere=bool(elsewhere), unread=bool(unread),
        any_check=bool(found or merged_checks or named_count(check_commands, texts.values())), has_files=bool(files), external=external,
        missing=miss_t, may_pass=may_pass)
    lint_runs, lint_basis = _lint_state(lint_t, _max(may_t, may_check_t), elsewhere=bool(elsewhere),
                                        partial=partial_merge, unread=bool(unread),
                                        has_files=bool(files), external=external,
                                        missing=lint_miss_t)
    # The #926 null codes apply only where no lint line runs at all: a masked linter stays
    # not_enforced (false), never not measured, whatever the build beside it does.
    if lint_basis == "no_lint_command":
        gate = lint_gate(root, tracked, compiler=bool(gate_t[0] or gate_t[1]),
                         dotnet=bool(dotnet_t[0] or dotnet_t[1]))
        if gate is not None:
            lint_runs, lint_basis = None, gate
        elif gate_t[2] or gate_t[3]:
            lint_basis = "not_enforced"   # a masked or off-trigger compiler gate
    return {"available": True, "ci_files": len(files), "check_commands": checks,
            "included_files": included, "included_check_commands": included_checks,
            "resolved_includes": sum(r["status"] in SETTLED for r in records),
            "unresolved_includes": sum(r["status"] not in SETTLED for r in records),
            "includes": records, "merged_config": merged.status,
            "merged_config_reason": merged.reason, "merged_check_commands": merged_checks,
            "test_or_lint_configured": runs, "basis": basis,
            "enforced_check_runs": enforced_runs, "enforced_basis": enforced_basis,
            "masked_check_commands": masked, "off_trigger_check_commands": off,
            "lint_runs": lint_runs, "lint_basis": lint_basis,
            "unread_ci_files": unread}
