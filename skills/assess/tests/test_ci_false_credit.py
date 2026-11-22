"""CI lines that run but cannot fail their job, or never run, earn no credit (#919).

Five holes found by the #776 parent check: a version-only step, an exit-zero
flag, a later ``exit 0`` in a shell without errexit (``pwsh``, a Windows runner,
``set +e``; GitHub's default ``bash -e`` stops at the failing check first), a
literal ``if: false`` and a workflow in a subfolder of ``.github/workflows/``. Each reads unmet for Layer 3 level 1 (``lint_runs``)
and Layer 5 level 1 (``enforced_check_runs``).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.check_command import is_info_invocation, is_lint_run
from lib.ci_checks import check_commands, enforced_check_commands, scan_ci_checks
from lib.command_index import ci_config_files


def _wf(run: str) -> str:
    return f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {run}\n"


def _block(text: str, shell: str = "") -> str:
    body = "".join(f"          {line}\n" for line in text.splitlines())
    step_shell = f"        shell: {shell}\n" if shell else ""
    return f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: |\n{body}{step_shell}"


def _shell(run: str, step: str = "", runs_on: str = "ubuntu-latest", job: str = "") -> str:
    """One ``run:`` step with an optional step ``shell:`` line, job keys and ``runs-on``."""
    step = step.replace("    shell:", "        shell:") if step else ""
    return (f"on: pull_request\njobs:\n  t:\n    runs-on: {runs_on}\n{job}    steps:\n"
            f"      - run: {run}\n{step}")


def _scan(tmp_path: Path, files: dict[str, str]) -> tuple[bool | None, bool | None]:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, encoding="utf-8", errors="replace")
    block = scan_ci_checks(tmp_path)
    return block["enforced_check_runs"], block["lint_runs"]


@pytest.mark.parametrize("text", [
    "ruff --version", "pytest --version", "uv run tox --version", "ruff check --help", "ruff -V",
    "python -m pytest -V", "pytest -h", "pytest --collect-only", "pytest --co -q",
    "uv run pytest --collect-only tests/", "make test ARGS=x --help",
])
def test_an_info_invocation_runs_no_check(text: str) -> None:
    assert is_info_invocation(text)
    assert check_commands(_wf(text)) == 0
    assert not is_lint_run(text)


@pytest.mark.parametrize("text", [
    "mvn -B -V verify", "./gradlew -V test", "./scripts/test.sh -V", "pytest -v", "ruff check .",
])
def test_a_flag_that_is_not_a_version_query_still_counts(text: str) -> None:
    assert not is_info_invocation(text)
    assert check_commands(_wf(text)) == 1


def test_ctest_v_is_verbose_not_a_version_query() -> None:
    assert not is_info_invocation("ctest -V")


@pytest.mark.parametrize("text", [
    "dart test --help", "flutter test --help", "flutter test -h", "dotnet test --list-tests",
    "rspec --init", "bundle exec rspec --init", "ctest --version", "go test --help",
    "cargo test --help", "mvn --version", "./gradlew --help",
])
def test_a_runner_asked_for_usage_or_a_test_list_is_an_info_invocation(text: str) -> None:
    """The #924 runners and the ones before them: whatever classifies the command,
    ``ci_checks`` drops an info invocation from every line test."""
    assert is_info_invocation(text)
    assert check_commands(_wf(text)) == 0
    assert enforced_check_commands(_wf(text)) == (0, 0)


@pytest.mark.parametrize("text", [
    "rspec", "dotnet test", "flutter test", "rspec spec/init_spec.rb",
    "./scripts/test.sh -h db", "./scripts/integration-test.sh -h localhost", "bash scripts/test.sh -h db",
    "./scripts/test.sh --co", "./scripts/test.sh --list-tests", "./scripts/test.sh --collect-only",
    "mvn -B -V verify", "./gradlew -V test", "tox -e py -- --co",
])
def test_the_same_runners_running_tests_are_not_info_invocations(text: str) -> None:
    assert not is_info_invocation(text)


@pytest.mark.parametrize("text", [
    pytest.param(_wf("ruff check . --exit-zero"), id="ruff_exit_zero"),
    pytest.param(_wf("flake8 --exit-zero ."), id="flake8_exit_zero"),
    pytest.param(_wf("golangci-lint run --issues-exit-code=0"), id="golangci_equals"),
    pytest.param(_wf("golangci-lint run --issues-exit-code 0"), id="golangci_space"),
    pytest.param(_shell("pytest; exit 0", "    shell: pwsh\n"), id="pwsh_semicolon_exit_0"),
    pytest.param(_shell("ruff check .; exit 0", "    shell: powershell\n"), id="powershell_exit_0"),
    pytest.param(_shell("pytest; exit 0", "    shell: bash {0}\n"), id="custom_bash_without_e"),
    pytest.param(_shell("pytest; exit 0", runs_on="windows-latest"), id="windows_runner_default_pwsh"),
    pytest.param(_shell("pytest; exit 0", runs_on="[self-hosted, windows]"), id="windows_label_list"),
    pytest.param(_shell("pytest; exit 0", job="    defaults:\n      run:\n        shell: pwsh\n"),
                 id="job_defaults_pwsh"),
    pytest.param("defaults:\n  run:\n    shell: pwsh\n" + _wf("pytest; exit 0"), id="workflow_defaults_pwsh"),
    pytest.param(_block("set +e\npytest\necho done\nexit 0"), id="set_plus_e_then_exit_0"),
    pytest.param(_block("pytest\nexit 0", shell="pwsh"), id="pwsh_block_exit_0"),
    pytest.param(_shell("pytest; exit 0", "    shell: powershell -NoProfile -ExecutionPolicy Bypass -File {0}\n"),
                 id="custom_powershell_command"),
    pytest.param(_shell("pytest; exit 0", "    shell: C:\\tools\\pwsh.exe -Command {0}\n"),
                 id="custom_pwsh_path_and_exe"),
    pytest.param(_shell("pytest; exit 0", "    shell: cmd /c {0}\n"), id="custom_cmd"),
    pytest.param(_block("if ($env:X) { Write-Host hi }\npytest\nexit 0", shell="pwsh"),
                 id="pwsh_one_line_if_then_exit_0"),
    pytest.param(_block("if ($env:X) {\n  Write-Host hi\n}\npytest\nexit 0", shell="pwsh"),
                 id="pwsh_block_if_then_exit_0"),
    pytest.param(_block("( cd sub && make build )\npytest\nexit 0", shell="bash {0}"),
                 id="spaced_subshell_then_exit_0"),
    pytest.param(_block("{ echo a; echo b; }\npytest\nexit 0", shell="bash {0}"),
                 id="brace_group_then_exit_0"),
    pytest.param(_block('case "$X" in\n  a) echo a ;;\nesac\npytest\nexit 0', shell="bash {0}"),
                 id="closed_case_then_exit_0"),
    pytest.param(_block("f() { echo hi; }\necho $(date) ${HOME}\npytest\nexit 0", shell="dash {0}"),
                 id="one_line_function_and_substitutions_then_exit_0"),
    pytest.param("jobs:\n  t:\n    runs-on: x\n    steps:\n      - if: false\n        run: pytest\n",
                 id="step_if_false"),
    pytest.param("jobs:\n  t:\n    if: false\n    runs-on: x\n    steps:\n      - run: pytest\n",
                 id="job_if_false"),
    pytest.param("jobs:\n  t:\n    if: ${{ false }}\n    runs-on: x\n    steps:\n      - run: pytest\n",
                 id="job_if_expression_false"),
    pytest.param("jobs:\n  t:\n    runs-on: x\n    steps:\n      - run: pytest\n        if: \"${{false}}\"  # off\n",
                 id="step_if_quoted_expression_false"),
])
def test_a_check_that_cannot_fail_or_never_runs_is_masked(text: str) -> None:
    assert check_commands(text) == 1
    assert enforced_check_commands(text) == (0, 1)


@pytest.mark.parametrize("text", [
    pytest.param(_wf("pytest; exit 0"), id="default_bash_stops_at_the_failing_check"),
    pytest.param(_wf("ruff check .; exit 0"), id="default_bash_lint_then_exit_0"),
    pytest.param(_block("pytest\necho done\nexit 0"), id="default_bash_block_exit_0"),
    pytest.param(_shell("pytest; exit 0", "    shell: bash\n"), id="named_bash"),
    pytest.param(_shell("pytest; exit 0", "    shell: sh\n"), id="named_sh"),
    pytest.param(_shell("pytest; exit 0", "    shell: bash -e {0}\n"), id="custom_bash_with_e"),
    pytest.param(_shell("pytest; exit 0", "    shell: bash --noprofile --norc -eo pipefail {0}\n"),
                 id="custom_bash_eo_pipefail"),
    pytest.param(_shell("pytest; exit 0", runs_on="${{ matrix.os }}"), id="runs_on_expression_not_read"),
    pytest.param("test:\n  script:\n    - pytest\n    - exit 0\n", id="gitlab_items_are_each_checked"),
    pytest.param("test:\n  script:\n    - pytest; exit 0\n", id="gitlab_semicolon_exit_0"),
    pytest.param(_block("cat > s.sh <<EOF\nexit 0\nEOF\npytest", shell="pwsh"), id="exit_0_in_a_heredoc"),
    pytest.param(_block("pytest\ncat > x.sh <<'EOF'\nexit 0\nEOF"), id="heredoc_after_check_default_shell"),
    pytest.param(_block("pytest\ncat > x.sh <<'EOF'\nexit 0\nEOF", shell="pwsh"), id="heredoc_after_check_pwsh"),
    pytest.param(_block("pytest\npython -c '\nimport sys\nexit 0\n'", shell="pwsh"),
                 id="multi_line_quote_after_check_pwsh"),
    pytest.param(_block("cat > s.sh <<-'END'\n  exit 0\n  END\npytest", shell="pwsh"),
                 id="exit_0_in_a_quoted_heredoc"),
    pytest.param(_block('python -c "\nimport sys\nexit 0\n"\npytest', shell="pwsh"),
                 id="exit_0_in_a_multi_line_string"),
    pytest.param(_shell("pytest || exit 1; exit 0", "    shell: pwsh\n"), id="own_reraise_before_exit_0"),
    pytest.param(_wf("pytest; echo exit 0"), id="exit_0_as_an_argument"),
    pytest.param(_shell("pytest; [ -f x ] && exit 0", "    shell: bash {0}\n"), id="conditional_exit_0"),
    pytest.param(_block('if [ -z "$X" ]; then exit 0; fi\npytest', shell="bash {0}"),
                 id="early_exit_guard_one_line"),
    pytest.param(_block('if [ -z "$X" ]; then\n  exit 0\nfi\npytest', shell="bash {0}"),
                 id="early_exit_guard_block"),
    pytest.param(_block("cleanup() {\n  exit 0\n}\npytest", shell="bash {0}"), id="exit_0_in_a_function"),
    pytest.param(_block('case "$X" in\n  a)\n    exit 0\n    ;;\nesac\npytest', shell="bash {0}"),
                 id="exit_0_in_a_case_branch"),
    pytest.param(_block("(\n  cd sub\n  exit 0\n)\npytest", shell="bash {0}"), id="exit_0_in_a_subshell"),
    pytest.param(_block("pytest\nif ($env:SKIP) {\n  exit 0\n}", shell="pwsh"), id="pwsh_guard_after_check"),
    pytest.param(_block("pytest\nif ($env:SKIP) { exit 0 }", shell="pwsh"), id="pwsh_one_line_guard_after_check"),
    pytest.param(_block("pytest\nif ($env:SKIP) {\n  exit 0\n}", shell="powershell"),
                 id="powershell_guard_after_check"),
    pytest.param(_shell("pytest; exit 0", "    shell: zsh -e {0}\n"), id="custom_zsh_with_e"),
    pytest.param(_shell("pytest; exit 0", "    shell: /bin/bash -eu {0}\n"), id="custom_bash_path_eu"),
    pytest.param(_shell("pytest; exit 0", "    shell: node {0}\n"), id="other_program_not_read"),
    pytest.param("jobs:\n  t:\n    runs-on: x\n    steps:\n      - run: pytest\n      - run: exit 0\n",
                 id="exit_0_in_another_step"),
    pytest.param("jobs:\n  t:\n    if: github.event_name == 'pull_request'\n    runs-on: x\n"
                 "    steps:\n      - run: pytest\n", id="job_if_condition"),
    pytest.param("jobs:\n  t:\n    if: ${{ 'false' }}\n    runs-on: x\n    steps:\n      - run: pytest\n",
                 id="job_if_non_empty_string_is_truthy"),
])
def test_a_check_that_can_fail_stays_enforced(text: str) -> None:
    assert enforced_check_commands(text) == (1, 0)


def test_an_exit_zero_flag_masks_only_its_own_segment() -> None:
    assert enforced_check_commands(_wf("ruff check . --exit-zero && pytest")) == (1, 0)


def test_a_workflow_in_a_subfolder_is_not_a_ci_file() -> None:
    files = [".github/workflows/ci.yml", ".github/workflows/sub/ci.yml", ".github/workflows/a.yaml",
             ".gitlab-ci.yml", ".github/workflows/x/y/z.yml"]
    assert ci_config_files(files) == [".github/workflows/a.yaml", ".github/workflows/ci.yml", ".gitlab-ci.yml"]


@pytest.mark.parametrize("files", [
    pytest.param({".github/workflows/ci.yml": _wf("ruff --version")}, id="version_only"),
    pytest.param({".github/workflows/ci.yml": _wf("ruff check . --exit-zero")}, id="exit_zero"),
    pytest.param({".github/workflows/ci.yml": _shell("ruff check .; pytest; exit 0", runs_on="windows-latest")},
                 id="exit_0_on_windows"),
    pytest.param({".github/workflows/ci.yml": (
        "on: pull_request\njobs:\n  t:\n    if: false\n    runs-on: x\n    steps:\n"
        "      - run: ruff check .\n      - run: pytest\n")}, id="if_false"),
    pytest.param({".github/workflows/sub/ci.yml": _wf("ruff check . && pytest")}, id="subfolder"),
])
def test_each_hole_reads_unmet_for_layer_3_and_layer_5(tmp_path: Path, files: dict[str, str]) -> None:
    assert _scan(tmp_path, files) == (False, False)


def test_the_plain_checks_still_read_met(tmp_path: Path) -> None:
    assert _scan(tmp_path, {".github/workflows/ci.yml": _block("ruff check .\npytest")}) == (True, True)


def test_a_script_with_a_host_flag_is_still_a_check() -> None:
    assert check_commands(_wf("./scripts/test.sh -h db")) == 1
    assert enforced_check_commands(_wf("./scripts/test.sh -h db")) == (1, 0)
