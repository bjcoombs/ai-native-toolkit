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
  way instruction-file commands are (``command_resolver.extract_commands``),
  and classified with ``check_command.is_check_command``: any test or
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
  fenced block inside a script cannot swallow the lines after it; a command
  continued with ``\\`` is read from its first line.
- Triggers are not read: a check in a workflow that runs only on push counts
  the same as one on pull requests.

GitLab includes are resolved offline where the included file is on disk
(``lib/ci_includes.py``, #766): ``local:`` files of the checkout, and
``project:`` / ``component:`` files of a clone in the ghsync root passed as
``ghsync_root`` (``assess_signals --ghsync-root``; ``fleet.py`` passes its
ROOT). Each resolved file's lines are classified the same way and counted in
``included_check_commands``. ``includes`` lists one record per include item
(kind, id, status, ref_basis, depth; never a file name or text), with the
``resolved_includes`` / ``unresolved_includes`` counts.

Under ``--platform`` (and in a full ``/assess`` run) a repository whose
includes did not all resolve offline also gets GitLab's merged configuration
from the CI lint API (``lib/gitlab_ci_lint.py``, #796). Its check lines are
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
kept off by triggers like any step. A lone ``- flake8`` item counts under a
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
lint_basis, unread_ci_files}`. Each line,
with its `- ` / `run:` / `script:` lead and a trailing `\\` dropped
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
Tests: `tests/test_ci_checks.py` (`test_lint_runs_states` is the #782 fixture table),
`tests/test_external_ci.py` (#928), `tests/test_ci_lint_gates.py` (the #926 fixtures),
`tests/test_gitlab_ci_lint.py`, and the Layer 3 and Layer 5 rows in
`tests/test_level_signals.py`.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from lib.check_command import (
    BARE_RUN_PROGRAMS, LINT_PROGRAMS, TYPE_PROGRAMS, is_check_command, is_info_invocation,
    is_lint_command, is_lint_run, may_check, may_lint)
from lib.ci_includes import IncludeRecord, Resolution, resolve_includes
from lib.ci_lint_gates import (
    line_commands, line_compiler_gate, line_dotnet_build, lint_gate, rewrite_gated)
from lib.ci_masking import unmasked_lines
from lib.named_check import (
    instruction_words, named_count, setup_ci_target, sourced_env, unread_ci_target)
from lib.ci_triggers import Trigger, gitlab_line_triggers, keep, resolve_workflows, workflow_trigger
from lib.ci_scripts import Present, ScriptTree, committed, runs_missing, script_runs
from lib.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.command_resolver import command_words, dot_as_source, extract_commands
from lib.external_ci_rules import as_external
from lib.lint_vocabulary import dotnet_build
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
                        "external_ci_marker", "no_check_command", "no_ci"]
EnforcedBasis = Literal[
    "enforced_check", "included_jobs", "unknown_trigger", "script_not_found", "check_target",
    "unread_ci_system", "external_ci", "external_ci_marker", "not_enforced", "no_check_command",
    "no_ci"]
LintBasis = Literal["lint_command", "included_jobs", "merged_config_partial", "unknown_trigger",
                    "script_not_found", "check_target", "unread_ci_system", "external_ci",
                    "external_ci_marker", "analysers_in_build", "compiler_gate_only",
                    "not_enforced", "no_lint_command", "no_ci"]
# Off-repository CI, in precedence order (#928): platform evidence, then the Prow marker.
External = Literal["external_ci", "external_ci_marker"] | None
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


# A YAML list item with no key (``- mypy``), as opposed to ``- run: ...``.
_BARE_ITEM = re.compile(r"^\s*-\s+(?![\w.-]+:(?:\s|$))")
# A GitLab key whose list items are shell lines (``script:``, ``  before_script: &s``).
_SCRIPT_KEY = re.compile(r"^\s*(?:before_script|after_script|script):\s*(?:&\S+\s*)?(?:#.*)?$")


def _line_runs_check(line: str, *, script_item: bool = True, present: Present | None = None) -> bool:
    """One CI line, classified on its own so an open quote or a stray fence cannot carry over.

    ``script_item`` is false for a bare list item whose parent key is not a script key
    (``_script_items``): a lone ``- pytest`` there is a value, not a run. With ``present``,
    a check that runs a script the tree does not hold is not counted (#937).
    """
    text = ci_line_text(line).removesuffix("\\").strip()
    if not text:
        return False
    commands = extract_commands(f"```bash\n{text}\n```\n")
    bare_item = _BARE_ITEM.match(line) is not None
    return any(
        not is_info_invocation(c.text) and committed(c.text, present) and (
            ((is_check_command(c.text) and not setup_ci_target(c.text)
              or script_runs(c.text, present, is_check_command))   # a read body (#1033)
             and not (bare_item and not script_item and _lone_check(c.text)))
            or (is_lint_command(c.text) and not (bare_item and _lone_linter(c.text))))
        for c in commands
    )


# GitHub Actions that run a linter as a step (#782): ``uses: <one of these>@<ref>``.
# An unknown third-party action is not read.
LINT_ACTIONS = frozenset({
    "golangci/golangci-lint-action", "astral-sh/ruff-action", "chartboost/ruff-action",
    "github/super-linter", "super-linter/super-linter",
    "wearerequired/lint-action", "davidanson/markdownlint-cli2-action",
})
# reviewdog actions that install or post, not lint.
_REVIEWDOG_NOT_LINT = frozenset({"reviewdog/action-setup", "reviewdog/action-suggester"})
# Actions that run a check whose config may or may not lint: ``pre-commit/action`` runs
# ``pre-commit run --all-files``, read like that line (``check_command.may_lint``).
MAY_LINT_ACTIONS = frozenset({"pre-commit/action"})
_USES = re.compile(r"""^\s*(?:-\s+)?uses:\s*["']?([\w.-]+/[\w.-]+)(?:/[^@\s"']*)?@""")


def _action(line: str) -> str:
    """The lower-cased ``owner/name`` a workflow step's ``uses:`` line calls, or empty."""
    match = _USES.match(line)
    return match.group(1).lower() if match else ""


def lint_action(line: str) -> bool:
    """A workflow step ``uses:`` line calling a known lint action (``reviewdog/action-*`` too,
    apart from the setup and suggester actions)."""
    name = _action(line)
    return name in LINT_ACTIONS or (name.startswith("reviewdog/action-")
                                    and name not in _REVIEWDOG_NOT_LINT)


def _line_runs_lint(line: str, *, script_item: bool = True, present: Present | None = None) -> bool:
    """One CI line that runs a linter (#782): ``is_lint_run``, never a bare ``- mypy`` item,
    or a ``uses:`` step of a known lint action (``LINT_ACTIONS``). With ``present``, a lint
    script the tree does not hold is not counted (#937)."""
    if lint_action(line):
        return True
    bare_item = _BARE_ITEM.match(line) is not None
    # A lone ``- mypy`` is a value under matrix: / needs: / stages:, but runs under a
    # GitLab script: key (``_script_items``).
    return any((is_lint_run(c.text) or script_runs(c.text, present, is_lint_run)) and committed(c.text, present)
               and not (bare_item and not script_item and _lone_linter(c.text))
               for c in line_commands(line))


def _line_may_lint(line: str, *, script_item: bool = True) -> bool:
    """One CI line whose check target or script may run a linter inside it (#782),
    or a ``uses:`` step of an action that does (``MAY_LINT_ACTIONS``)."""
    if _action(line) in MAY_LINT_ACTIONS:
        return True
    bare_item = _BARE_ITEM.match(line) is not None
    return any(may_lint(c.text) and not is_info_invocation(c.text)
               and not (bare_item and not script_item and _lone_check(c.text))
               for c in line_commands(line))


def _line_may_check(line: str, *, script_item: bool = True) -> bool:
    """One CI line whose target's recipe is not read, so it may run a check (#920):
    ``make foo``, ``npm run foo``, ``./scripts/foo.sh``, ``docker compose run app``."""
    bare_item = _BARE_ITEM.match(line) is not None
    return any((may_check(c.text) or unread_ci_target(c.text))
               and not (setup_ci_target(c.text) or sourced_env(c.text))
               and not is_info_invocation(c.text)
               and not (bare_item and not script_item and _lone_check(c.text))
               for c in line_commands(line))


def _lone_linter(text: str) -> bool:
    """A linter or type-checker name with no argument (``- mypy``, ``- tsc``): under
    ``matrix:`` / ``needs:`` / ``stages:`` a value, not a run."""
    words = command_words(text)
    return len(words) == 1 and (words[0] in LINT_PROGRAMS or words[0] in TYPE_PROGRAMS)


def _lone_build(text: str) -> bool:
    """A lone .NET build program (``- msbuild``): under ``matrix:`` a value (#926)."""
    words = command_words(text)
    return len(words) == 1 and dotnet_build(words)


def _lone_check(text: str) -> bool:
    """A test program or target runner with no argument (``pytest``, ``tox``, ``rspec``,
    ``make``): a value unless under ``script:``."""
    words = command_words(text)
    return len(words) == 1 and (words[0] in BARE_RUN_PROGRAMS or "/" in words[0])


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _script_items(lines: Sequence[str]) -> list[bool]:
    """Per line: false for a bare list item whose parent key is not a GitLab script key.

    Read for a lone always-check item (``- pytest``) and a lone linter (``- mypy``,
    which only ``lint_runs`` reads by key: ``test_or_lint_configured`` never counts one).

    The parent is the nearest line above at the item's indent or less that is not a
    sibling item, skipping blank and comment lines and lines nested deeper (a sibling's
    block scalar). An indentless list (``script:`` then ``- pytest`` at its indent) works.
    """
    flags = [True] * len(lines)
    for i, line in enumerate(lines):
        text = ci_line_text(line)
        if _BARE_ITEM.match(line) is None or not (_lone_check(text) or _lone_linter(text)
                                                  or _lone_build(text)):
            continue   # only a lone always-check, linter or build item needs its parent
        depth = _indent(line)
        parent = ""
        for above in reversed(lines[:i]):
            stripped = above.strip()
            if not stripped or stripped.startswith("#") or _indent(above) > depth:
                continue
            if _indent(above) == depth and _BARE_ITEM.match(above) is not None:
                continue
            parent = above
            break
        flags[i] = _SCRIPT_KEY.match(parent) is not None
    return flags


LineTest = Callable[..., bool]


def count_check_lines(lines: Sequence[str], *, context: Sequence[str] | None = None,
                      test: LineTest = _line_runs_check, extra: frozenset[int] = frozenset()) -> int:
    """How many of one CI file's lines run a test or lint check (a blanked line runs none).

    ``context`` is the file's original lines when ``lines`` is a rewrite of them with
    the same length (``ci_masking.unmasked_lines``): parent keys are read from it.
    ``test`` classifies one line (``_line_runs_lint`` and ``_line_may_lint`` for #782);
    a non-blank line whose index is in ``extra`` counts whatever it holds (a formatter
    rewrite a later ``git diff`` gate makes a lint run, ``ci_lint_gates.rewrite_gated``).
    """
    flags = _script_items(lines if context is None else context)
    return sum(1 for i, (line, script_item) in enumerate(zip(lines, flags, strict=True))
               if (i in extra and line.strip()) or test(line, script_item=script_item))


def check_commands(text: str) -> int:
    """How many lines of one CI file's text run a test or lint check."""
    return count_check_lines(text.splitlines())


def enforced_check_commands(text: str) -> tuple[int, int]:
    """``(unmasked, masked)``: the check lines of one CI file that can fail their job, and the rest."""
    lines = text.splitlines()
    total = count_check_lines(lines)
    enforced = count_check_lines(unmasked_lines(text), context=lines)
    return enforced, max(total - enforced, 0)


Tally = tuple[int, int, int, int]


def _is_lint_test(test: LineTest) -> bool:
    """``_line_runs_lint``, bare or bound to a ``present`` predicate (#937)."""
    return getattr(test, "func", test) is _line_runs_lint


def _trigger_tally(text: str, workflow: Trigger | None,
                   tests: Sequence[LineTest] = (_line_runs_check,)) -> list[Tally]:
    """Per line test: ``(ordinary, unknown trigger, off trigger, masked)`` lines of one CI file.

    ``workflow`` is a GitHub workflow's settled trigger; None reads GitLab job triggers.
    The lint test also counts the rewrite lines a ``git diff`` gate makes lint runs (#926).
    """
    lines = text.splitlines()
    gated = rewrite_gated(text) if any(_is_lint_test(t) for t in tests) else frozenset()
    unmasked = unmasked_lines(text)
    triggers: list[Trigger] = (gitlab_line_triggers(text) if workflow is None
                               else [workflow] * len(lines))
    kept = [keep(unmasked, triggers, t) for t in ("ordinary", "unknown", "not_ordinary")]
    out: list[Tally] = []
    for test in tests:
        extra = gated if _is_lint_test(test) else frozenset()
        counts = [count_check_lines(k, context=lines, test=test, extra=extra) for k in kept]
        masked = max(count_check_lines(lines, test=test, extra=extra) - sum(counts), 0)
        out.append((counts[0], counts[1], counts[2], masked))
    return out


def _basis(found: bool, merged: bool, *, elsewhere: bool, partial: bool, unread: bool,
           files: bool, external: External = None) -> tuple[bool | None, CiChecksBasis]:
    """``(test_or_lint_configured, basis)``: an own or included check, the merged one, pending
    included jobs, an unread CI system (#787: the value stays false, its meaning unchanged)."""
    if found:
        return True, "check_command"
    if merged:
        return True, "merged_config"
    if elsewhere:
        return None, "merged_config_partial" if partial else "included_jobs"
    if unread:
        return False, "unread_ci_system"
    if external:
        return False, external
    return False, "no_check_command" if files else "no_ci"


@dot_as_source()   # . ./test.sh reads as source ./test.sh on a CI line (#1033)
def scan_ci_checks(repo_root: Path, ghsync_root: Path | None = None,
                   merged_ci: object = None, external_ci: object = None) -> CiChecksBlock:
    """Build the ``ci_checks`` block for the repository at ``repo_root``. ``merged_ci`` is the
    ``gitlab_merged_ci`` reading (``lib/gitlab_ci_lint.py``) and ``external_ci`` the
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
    for rel in files:
        text = read_config(root, rel)
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
    texts = {rel: read_config(root, rel) for rel in files}
    workflows = resolve_workflows(
        {rel: workflow_trigger(texts[rel]) for rel in files if rel not in CI_CONFIG_FILES}, texts)
    present = ScriptTree(root, tracked)
    tests: tuple[LineTest, ...] = tuple(
        partial(t, present=present) for t in (_line_runs_check, _line_runs_lint,
                                              instruction_words(runs_missing(_line_runs_check)),
                                              runs_missing(_line_runs_lint))
    ) + (_line_may_lint, _line_may_check, line_compiler_gate, line_dotnet_build)
    tallies = [_trigger_tally(texts[rel], workflows.get(rel), tests) for rel in files]
    tallies += [_trigger_tally(f.text, None, tests) for f in resolution.files] if resolution else []
    check_t, lint_t, miss_t, lint_miss_t, may_t, may_check_t, gate_t, dotnet_t = (
        _sum(t[i] for t in tallies) for i in range(len(tests)))
    ordinary, unknown, off, masked = check_t
    if merged.read and merged.text is not None:
        # GitLab's merged configuration (#796) repeats the file's own jobs: read it only
        # where the files alone found no enforced check, as basis reads merged_config.
        m_check, m_lint, m_miss, m_lint_miss, m_may, m_may_check, m_gate, m_dotnet = _trigger_tally(
            merged.text, None, tests)
        m_ordinary, m_unknown, m_off, m_masked = m_check
        if not lint_t[0]:
            lint_t, may_t = _max(lint_t, m_lint), _max(may_t, m_may)
            lint_miss_t = _max(lint_miss_t, m_lint_miss)
            gate_t, dotnet_t = _max(gate_t, m_gate), _max(dotnet_t, m_dotnet)
        if not ordinary:
            ordinary, unknown = m_ordinary, max(unknown, m_unknown)
            may_check_t, miss_t = _max(may_check_t, m_may_check), _max(miss_t, m_miss)
        if not (checks or included_checks):
            off, masked = m_off, m_masked
    found = bool(checks or included_checks or any(check_t))   # a read script body (#1033)
    runs, basis = _basis(found, bool(merged_checks), elsewhere=bool(elsewhere), partial=partial_merge,
                         unread=bool(unread), files=bool(files), external=external)
    enforced_runs, enforced_basis = _enforced_state(
        ordinary, unknown, may_check_t, elsewhere=bool(elsewhere), unread=bool(unread),
        any_check=bool(found or merged_checks or named_count(check_commands, texts.values())), has_files=bool(files), external=external,
        missing=miss_t)
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
            "resolved_includes": sum(r["status"] == "resolved" for r in records),
            "unresolved_includes": sum(r["status"] != "resolved" for r in records),
            "includes": records, "merged_config": merged.status,
            "merged_config_reason": merged.reason, "merged_check_commands": merged_checks,
            "test_or_lint_configured": runs, "basis": basis,
            "enforced_check_runs": enforced_runs, "enforced_basis": enforced_basis,
            "masked_check_commands": masked, "off_trigger_check_commands": off,
            "lint_runs": lint_runs, "lint_basis": lint_basis,
            "unread_ci_files": unread}


def _sum(tallies: Iterable[Tally]) -> Tally:
    a = b = c = d = 0
    for t in tallies:
        a, b, c, d = a + t[0], b + t[1], c + t[2], d + t[3]
    return a, b, c, d


def _max(x: Tally, y: Tally) -> Tally:
    return max(x[0], y[0]), max(x[1], y[1]), max(x[2], y[2]), max(x[3], y[3])


def _enforced_state(ordinary: int, unknown: int, may_check: Tally, *, elsewhere: bool,
                    unread: bool, any_check: bool, has_files: bool,
                    external: External = None,
                    missing: Tally = (0, 0, 0, 0)) -> tuple[bool | None, EnforcedBasis]:
    """``enforced_check_runs`` and ``enforced_basis`` (#775); unknown beats wrong, so an
    unread CI system (#787) or an unmasked target whose recipe is not read (#920) reads
    null ahead of a masked or off-trigger check."""
    if ordinary:
        return True, "enforced_check"
    if elsewhere:
        return None, "included_jobs"
    if unknown:
        return None, "unknown_trigger"
    if missing[0] or missing[1]:
        return None, "script_not_found"
    if may_check[0] or may_check[1]:
        return None, "check_target"
    if unread:
        return None, "unread_ci_system"
    if external:
        return None, external  # off-repository CI (#928): never credit, never false
    if any_check:
        return False, "not_enforced"
    return False, "no_check_command" if has_files else "no_ci"


def _lint_state(lint: Tally, may: Tally, *, elsewhere: bool, partial: bool, unread: bool,
                has_files: bool, external: External = None,
                missing: Tally = (0, 0, 0, 0)) -> tuple[bool | None, LintBasis]:
    """``lint_runs`` and ``lint_basis`` from the lint and may-lint tallies (#782); an unread
    CI system (#787) reads null ahead of the false cases, as for ``enforced_check_runs``."""
    ordinary, unknown, off, masked = lint
    if ordinary:
        return True, "lint_command"
    if elsewhere:
        return None, "merged_config_partial" if partial else "included_jobs"
    if unknown:
        return None, "unknown_trigger"
    if missing[0] or missing[1]:
        return None, "script_not_found"
    if may[0] or may[1]:
        return None, "check_target"
    if unread:
        return None, "unread_ci_system"
    if external:
        return None, external
    if not has_files:
        return False, "no_ci"
    return False, "not_enforced" if off or masked else "no_lint_command"
