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
from lib.ci_exempt import exempt_segments, unquoted
from lib.ci_masking import split_statements, unmasked_lines
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


# The #920 runners and wrappers (#924): each is a check alone, and the info flags drop it.
_RUNNER_INFO = [
    ("swift test", "swift test --help"), ("swift test", "swift test --list-tests"),
    ("mix test", "mix test --help"), ("deno test", "deno test --help"),
    ("deno test", "deno test -h"), ("vendor/bin/phpunit", "vendor/bin/phpunit --version"),
    ("vendor/bin/phpunit", "vendor/bin/phpunit --list-tests"), ("ctest", "ctest --version"),
    ("ctest", "ctest -N"), ("bazel test //...", "bazel test --help"),
    ("dotnet test", "dotnet test --list-tests"), ("dotnet test", "dotnet test -h"),
    ("turbo run test", "turbo run test --help"), ("hatch test", "hatch test --help"),
    ("docker compose run app pytest", "docker compose run app pytest --co"),
    ("docker compose run app pytest", "docker compose run app pytest -h"),
    ("bundle exec rspec", "bundle exec rspec -h"), ("bundle exec rspec", "bundle exec rspec --init"),
    ("pnpm exec vitest run", "pnpm exec vitest --help"),
]


@pytest.mark.parametrize(("runs", "info"), _RUNNER_INFO, ids=[i for _, i in _RUNNER_INFO])
def test_a_runner_check_is_dropped_by_its_info_flag(runs: str, info: str) -> None:
    assert check_commands(_wf(runs)) == 1, runs
    assert is_info_invocation(info)
    assert check_commands(_wf(info)) == 0
    assert enforced_check_commands(_wf(info)) == (0, 0)


@pytest.mark.parametrize("text", [
    "ctest -V", "bundle exec rspec spec/a_spec.rb", "dotnet test -c Release",
])
def test_a_runner_flag_that_runs_the_work_stays_a_check(text: str) -> None:
    assert not is_info_invocation(text)
    assert check_commands(_wf(text)) == 1


def test_pytype_v_is_its_python_version_and_runs_the_type_check() -> None:
    assert not is_info_invocation("pytype -V 3.11 src/")
    assert is_lint_run("pytype -V 3.11 src/")


def test_a_may_check_target_asking_for_help_is_not_a_pending_check(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _wf("make --help")}
    assert _scan(tmp_path, files) == (False, False)


# Issue #936: under errexit (GitHub's default ``bash -e {0}``) a failing command does not stop
# the step in an errexit-exempt position (bash manual, ``set -e``): the left side of ``&&`` /
# ``||``, a pipeline but its last command (without pipefail), an ``if`` / ``elif`` / ``while``
# condition, or after ``!``. A later top-level ``exit 0`` then masks it.
@pytest.mark.parametrize("text", [
    pytest.param(_wf("pytest && echo ok; exit 0"), id="and_list_left_side"),
    pytest.param(_block("pytest && echo ok\nexit 0"), id="and_list_left_side_block"),
    pytest.param(_wf("pytest || echo failed; exit 0"), id="or_list_left_side"),
    pytest.param(_wf("pytest | tee log; exit 0"), id="pipe_without_pipefail_default_shell"),
    pytest.param(_shell("pytest | tee log; exit 0", "    shell: sh\n"), id="pipe_under_named_sh"),
    pytest.param(_block("set -o pipefail\nset +o pipefail\npytest | tee log\nexit 0"),
                 id="pipefail_turned_off_again"),
    pytest.param(_shell("pytest | tee log && echo ok; exit 0", "    shell: bash\n"),
                 id="pipeline_before_and_with_pipefail"),
    pytest.param(_shell('echo "{"; pytest; exit 0', "    shell: bash {0}\n"), id="quoted_brace_is_not_a_group"),
    pytest.param(_block('echo "<<EOF"\npytest\nexit 0', shell="bash {0}"), id="quoted_heredoc_is_not_a_heredoc"),
    pytest.param(_wf("ruff check . --exit-zero || pytest"), id="or_after_exit_zero_never_runs"),
    pytest.param(_wf("git diff | flake8 --diff --exit-zero"), id="piped_exit_zero_flag"),
    pytest.param(_block("if [ -n \"$X\" ]; then\n  pytest && echo ok\nfi\nexit 0"), id="and_list_in_an_if_body"),
])
def test_a_check_in_an_errexit_exempt_position_before_exit_0_is_masked(text: str) -> None:
    assert check_commands(text) == 1
    assert enforced_check_commands(text) == (0, 1)


@pytest.mark.parametrize("text", [
    pytest.param(_shell("pytest | tee log; exit 0", "    shell: bash\n"), id="pipe_with_named_bash_pipefail"),
    pytest.param(_shell("pytest | tee log; exit 0", "    shell: bash -eo pipefail {0}\n"),
                 id="pipe_with_custom_pipefail"),
    pytest.param(_block("set -euo pipefail\npytest | tee log\nexit 0"), id="pipe_after_set_euo_pipefail"),
    pytest.param(_block("set -o pipefail\npytest | tee log\nexit 0"), id="pipe_after_set_o_pipefail"),
    pytest.param(_wf("cd sub && pytest; exit 0"), id="check_after_the_final_and"),
    pytest.param(_wf("pytest && echo ok"), id="and_list_with_no_exit_0"),
    pytest.param(_wf("pytest || exit 1; exit 0"), id="own_reraise_default_shell"),
    pytest.param(_wf("pytest && echo ok || exit 1; exit 0"), id="and_list_with_reraise"),
    pytest.param(_wf("ruff check . --exit-zero | grep E || pytest"), id="exit_zero_not_ending_its_pipeline"),
    # #1008 moved ``pytest && echo ok; [ -f x ] && exit 0``, ``... && pytest || echo x``,
    # ``if pytest; then :; fi`` and ``{ pytest && echo ok; }; exit 0`` to not measured
    # (tests/test_ci_may_pass.py).
    # A subshell or a function returns the list's status as one command: errexit stops there.
    pytest.param(_wf("( pytest && echo ok ); exit 0"), id="and_list_in_a_subshell"),
    pytest.param(_block("(\n  pytest && echo ok\n)\nexit 0"), id="and_list_in_a_multi_line_subshell"),
    pytest.param(_block("run_tests() {\n  pytest && echo ok\n}\nrun_tests\nexit 0"), id="and_list_in_a_function"),
])
def test_a_check_errexit_still_stops_on_stays_enforced(text: str) -> None:
    assert enforced_check_commands(text) == (1, 0)


def test_only_the_exempt_segment_is_masked() -> None:
    assert enforced_check_commands(_wf("pytest && ruff check .; exit 0")) == (1, 0)
    assert unmasked_lines(_wf("pytest && ruff check .; exit 0"))[-1] == "      - run: ruff check .; exit 0"


def test_a_folded_block_is_read_as_one_line() -> None:
    folded = ("on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
              "      - run: >\n          pytest\n          || true\n")
    assert enforced_check_commands(folded) == (0, 1)
    pwsh = folded.replace("|| true", "exit 0") + "        shell: pwsh\n"
    assert enforced_check_commands(pwsh) == (1, 0)   # one command: ``pytest exit 0``
    kept = folded.replace(">\n", ">-\n").replace("|| true", "--cov\n\n          exit 0")
    assert enforced_check_commands(kept) == (1, 0)   # errexit stops at the failing ``pytest --cov``


@pytest.mark.parametrize("text", [
    "hatch run pytest --version", "pnpm exec jest --help", "hatch run pytest -V", "hatch run pytest --co",
])
def test_an_info_call_through_any_wrapper_is_an_info_invocation(text: str) -> None:
    assert is_info_invocation(text)


def test_exempt_segments_follow_the_bash_grammar() -> None:
    s = split_statements("a | b && c | d || e")[0]
    assert exempt_segments(s, pipefail=False) == {0, 1, 2, 3}
    assert exempt_segments(s, pipefail=True) == {0, 1, 2, 3}
    assert exempt_segments(split_statements("a | b")[0], pipefail=True) == set()
    assert exempt_segments(split_statements("a & b && c")[0], pipefail=True) == {1}
    assert exempt_segments(split_statements("! a")[0], pipefail=True) == {0}


def test_unquoted_drops_quoted_spans_and_escapes() -> None:
    assert unquoted("""echo "{" '(' \\{ x""") == "echo    x"


def _reads_pytest(text: str) -> bool:
    return any("pytest" in line for line in unmasked_lines(text))


# The line classifier reads no check behind ``if`` / ``while`` / ``until`` / ``!`` / ``do`` / ``{``, so these
# are read on ``unmasked_lines``: the condition is dropped before a later ``exit 0``.
@pytest.mark.parametrize("text", [
    pytest.param(_wf("if pytest; then :; fi; exit 0"), id="if_condition"),
    pytest.param(_block("if pytest\nthen\n  echo ok\nfi\nexit 0"), id="if_condition_block"),
    pytest.param(_block("if [ -n \"$X\" ]; then\n  echo x\nelif pytest; then\n  echo ok\nfi\nexit 0"),
                 id="elif_condition"),
    pytest.param(_wf("while pytest; do break; done; exit 0"), id="while_condition"),
    pytest.param(_wf("for d in a b; do pytest && echo ok; done; exit 0"), id="and_list_in_a_loop_body"),
    pytest.param(_wf("! pytest; exit 0"), id="negated"),
    pytest.param(_shell("! pytest | tee log; exit 0", "    shell: bash\n"), id="negated_pipeline_with_pipefail"),
])
def test_a_condition_or_negated_check_before_exit_0_is_dropped(text: str) -> None:
    assert not _reads_pytest(text)


@pytest.mark.parametrize("text", [
    pytest.param(_wf("if ! pytest; then exit 1; fi; exit 0"), id="if_not_check_then_exit_1"),
    pytest.param(_block("if pytest; then\n  echo ok\nelse\n  exit 1\nfi\nexit 0"), id="if_else_exit_1"),
    pytest.param(_wf("until pytest; do sleep 5; done; exit 0"), id="until_retries_until_it_passes"),
])
def test_a_condition_with_a_reraise_or_no_exit_0_is_kept(text: str) -> None:
    assert _reads_pytest(text)
