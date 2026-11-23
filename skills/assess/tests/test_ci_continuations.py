"""Tests for issue #799: backslash continuations, ``with:`` run inputs and script indirection in
``lib.ci_checks`` (``lib.ci_continuations``, ``lib.ci_action_inputs``, ``lib.ci_scripts``)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.check_command import is_check_command
from lib.ci_checks import check_commands, enforced_check_commands, scan_ci_checks
from lib.ci_continuations import join_continued
from lib.command_flags import maven_skips_tests
from lib.command_index import ci_line_text


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")


def _repo(root: Path, files: dict[str, str], executable: tuple[str, ...] = ()) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    if executable:
        _git(root, "update-index", "--chmod=+x", *executable)
    return root


def _gha(steps: str, on: str = "[pull_request]") -> dict[str, str]:
    return {".github/workflows/ci.yml": (
        f"on: {on}\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
        f"      - uses: actions/checkout@v4\n{steps}")}


def _reading(block: object) -> tuple[object, ...]:
    assert isinstance(block, dict)
    return (block["test_or_lint_configured"], block["enforced_check_runs"], block["enforced_basis"])


# --- join_continued ---------------------------------------------------------------------------

def test_join_keeps_line_count_and_is_idempotent() -> None:
    text = "run: |\n  mvn -B \\\n    -q \\\n    verify\n  echo done\n"
    joined = join_continued(text)
    assert joined.splitlines() == ["run: |", "  mvn -B -q verify", "", "", "  echo done"]
    assert join_continued(joined) == joined


@pytest.mark.parametrize(("text", "first"), [
    ("  a \\\\\n  b\n", "  a \\\\"),            # an even run is a literal backslash
    ("  # a comment \\\n  pytest\n", "  # a comment \\"),
    ("    a \\\n  next: key\n", "    a \\"),    # a shallower line is the next YAML key
    ("  a \\\n\n  b\n", "  a \\"),               # a blank line ends the command
])
def test_join_leaves_non_continuations(text: str, first: str) -> None:
    assert join_continued(text).splitlines()[0] == first


# --- the classifier on joined lines ------------------------------------------------------------

def test_expression_is_one_word_and_a_leading_variable_is_not_the_goal() -> None:
    assert ci_line_text("mvn -Dset=${{ matrix.set }} verify") == "mvn -Dset=${{matrix.set}} verify"
    assert is_check_command("mvn ${MVN_ARGS} -T8 verify")
    assert is_check_command("make $FLAGS test")
    assert not is_check_command("mvn ${MVN_ARGS} install")
    assert not is_check_command("mvn ${GOALS}")


@pytest.mark.parametrize(("words", "skips"), [
    ("mvn -DskipTests verify", True),
    ("./mvnw -Dmaven.test.skip=true verify", True),
    ("mvn -DskipTests=false verify", False),
    ("mvn -DskipITs verify", False),
    ("mvn -DskipTests checkstyle:check verify", False),
    ("mvn verify", False),
    ("gradle -DskipTests check", False),
])
def test_maven_skips_tests(words: str, skips: bool) -> None:
    assert maven_skips_tests(words.split()) is skips


def test_backslash_continued_mvn_verify_counts(tmp_path: Path) -> None:
    run = ("      - run: |\n          mvn ${MVN_ARGS} -T8 \\\n"
           "            -Dorg.slf4j.simpleLogger.showThreadName=true \\\n"
           "            -DmoduleSet=${{ matrix.moduleSet }} \\\n            verify\n")
    assert check_commands(run) == 1
    assert _reading(scan_ci_checks(_repo(tmp_path, _gha(run)))) == (True, True, "enforced_check")


@pytest.mark.parametrize("run", [
    "      - run: |\n          echo mvn \\\n            verify\n",
    "      - run: |\n          mvn -B \\\n            -DskipTests \\\n            install\n",
])
def test_a_continued_line_that_runs_no_check_is_not_credited(tmp_path: Path, run: str) -> None:
    assert check_commands(run) == 0
    assert _reading(scan_ci_checks(_repo(tmp_path, _gha(run))))[:2] == (False, False)


def test_a_continued_skip_tests_verify_reads_check_target(tmp_path: Path) -> None:
    run = "      - run: |\n          mvn -B \\\n            -DskipTests \\\n            verify\n"
    assert _reading(scan_ci_checks(_repo(tmp_path, _gha(run)))) == (False, None, "check_target")


@pytest.mark.parametrize("run", [
    "      - run: |\n          xvfb-run -a \\\n            npm test\n",
    "      - run: |\n          pytest \\\n            -q\n",
])
def test_continued_checks_stay_credited(tmp_path: Path, run: str) -> None:
    assert scan_ci_checks(_repo(tmp_path, _gha(run)))["test_or_lint_configured"] is True


def test_a_masked_continued_check_stays_masked() -> None:
    run = "      - run: |\n          mvn -B \\\n            verify \\\n            || true\n"
    assert enforced_check_commands(run) == (0, 1)


# --- run under with: ---------------------------------------------------------------------------

_COMMON = (".github/actions/common/action.yml",
           "name: common\ninputs:\n  run:\n    required: true\nruns:\n  using: composite\n"
           "  steps:\n    - shell: bash\n      run: ${{ inputs.run }}\n")


def test_run_input_under_with_counts(tmp_path: Path) -> None:
    steps = ("      - uses: ./.github/actions/common\n        with:\n          run: |\n"
             "            mvn ${MVN_ARGS} \\\n              verify\n")
    files = dict([_COMMON]) | _gha(steps)
    assert _reading(scan_ci_checks(_repo(tmp_path, files))) == (True, True, "enforced_check")


def test_run_input_of_a_masked_step_is_masked(tmp_path: Path) -> None:
    steps = ("      - uses: ./.github/actions/common\n        continue-on-error: true\n"
             "        with:\n          run: pytest\n")
    block = scan_ci_checks(_repo(tmp_path, dict([_COMMON]) | _gha(steps)))
    assert _reading(block) == (True, False, "not_enforced")
    assert block["masked_check_commands"] == 1


def test_run_input_of_an_unread_action_is_declared_not_run(tmp_path: Path) -> None:
    steps = "      - uses: someone/runner@v1\n        with:\n          run: pytest\n"
    assert _reading(scan_ci_checks(_repo(tmp_path, _gha(steps)))) == (True, None, "check_target")


def test_github_script_is_not_read_as_shell(tmp_path: Path) -> None:
    steps = ("      - uses: actions/github-script@v7\n        with:\n          script: |\n"
             "            pytest\n")
    block = scan_ci_checks(_repo(tmp_path, _gha(steps)))
    assert (block["check_commands"], *_reading(block)) == (0, False, False, "no_check_command")


def test_a_listed_runner_command_input_counts(tmp_path: Path) -> None:
    steps = ("      - uses: nick-fields/retry@v3\n        with:\n          timeout_minutes: 10\n"
             "          command: npm test\n")
    assert _reading(scan_ci_checks(_repo(tmp_path, _gha(steps)))) == (True, True, "enforced_check")


# --- script indirection ------------------------------------------------------------------------

_SCRIPT = "#!/bin/bash\nset -e\nmvn ${MVN_ARGS} -B verify\n"


def test_script_indirection_one_level_counts(tmp_path: Path) -> None:
    steps = ("      - uses: ./.github/actions/common\n        with:\n"
             "          run: etc/scripts/build.sh\n")
    files = dict([_COMMON]) | _gha(steps) | {"etc/scripts/build.sh": _SCRIPT}
    block = scan_ci_checks(_repo(tmp_path, files, executable=("etc/scripts/build.sh",)))
    assert _reading(block) == (True, True, "enforced_check")


def test_script_indirection_is_one_level(tmp_path: Path) -> None:
    files = _gha("      - run: bash etc/a.sh\n") | {
        "etc/a.sh": "set -e\nbash etc/b.sh\n", "etc/b.sh": _SCRIPT}
    block = scan_ci_checks(_repo(tmp_path, files))
    assert _reading(block) == (False, None, "check_target")   # a.sh is a script whose body is not a check


def test_script_indirection_does_not_follow_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "build.sh").write_text(_SCRIPT, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "etc").mkdir()
    try:
        (repo / "etc" / "go.sh").symlink_to(outside / "build.sh")
    except OSError:
        pytest.skip("symlinks are not available here")
    _repo(repo, _gha("      - run: bash etc/go.sh\n"))
    assert _reading(scan_ci_checks(repo)) == (False, None, "check_target")   # never the body outside
    absolute = _repo(tmp_path / "abs", _gha(f"      - run: bash {outside / 'build.sh'}\n"))
    assert scan_ci_checks(absolute)["test_or_lint_configured"] is False


# --- Done-When: the Helidon workflow shapes ----------------------------------------------------

_HELIDON = """name: "Validate"
on:
  pull_request:
  push:
    branches-ignore: [ 'main', 'helidon-*.x', 'release-*' ]
  workflow_call:
    inputs:
      ref:
        type: string
        default: ''
env:
  MVN_ARGS: |
    -B -fae -e
jobs:
  checkstyle:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ inputs.ref }}
      - uses: ./.github/actions/common
        with:
          run: etc/scripts/checkstyle.sh
  build:
    runs-on: ubuntu-22.04
    steps:
      - uses: ./.github/actions/common
        with:
          build-cache: read-write
          run: |
            mvn --version
            mvn ${MVN_ARGS} -T8 \\
              -Dorg.slf4j.simpleLogger.showThreadName=true \\
              -DskipTests \\
              -Ptests \\
              install
  _tests:
    needs: build
    strategy:
      matrix:
        os: [ ubuntu-22.04, windows-2022 ]
        moduleSet: [ core, it ]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: ./.github/actions/common
        with:
          build-cache: read-only
          run: |
            mvn ${MVN_ARGS} \\
              -DreactorRule=tests \\
              -DmoduleSet=${{ matrix.moduleSet }} \\
              -Dsurefire.reportNameSuffix=${{ matrix.platform }} \\
              verify
  _spotbugs:
    needs: build
    runs-on: ubuntu-22.04
    steps:
      - uses: ./.github/actions/common
        with:
          run: |
            mvn ${MVN_ARGS} -T8 \\
              -DmoduleSet=${{ matrix.moduleSet }} \\
              -DskipTests \\
              -Pspotbugs \\
              verify
"""


def test_helidon_shaped_workflow_reads_true(tmp_path: Path) -> None:
    files = {".github/workflows/validate.yml": _HELIDON,
             ".github/actions/common/action.yml": _COMMON[1],
             "etc/scripts/checkstyle.sh": "#!/bin/bash\nmvn ${MVN_ARGS} checkstyle:checkstyle-aggregate\n"}
    block = scan_ci_checks(_repo(tmp_path, files, executable=("etc/scripts/checkstyle.sh",)))
    assert block["check_commands"] == 1   # the _tests verify; spotbugs skips its tests
    assert _reading(block) == (True, True, "enforced_check")
