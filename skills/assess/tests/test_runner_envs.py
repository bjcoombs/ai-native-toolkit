"""tox environments and nox sessions read through on a CI line (issue #1180, ``lib.runner_envs``)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.check_command import is_lint_run
from lib.ci_checks import scan_ci_checks
from lib.runner_envs import env_commands, read_envs, reads_runner_envs

TOX_LINT = "[tox]\nenv_list = py, lint\n\n[testenv:lint]\ncommands =\n    ruff check {posargs:.}\n"
NOX_LINT = ("import nox\n\n@nox.session(python='3.12')\ndef lint(session):\n"
            "    session.install('ruff')\n    session.run('ruff', 'check', '.', *session.posargs)\n")


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _lint(tmp_path: Path, command: str, files: dict[str, str]) -> tuple[object, object]:
    ci = {".gitlab-ci.yml": f"lint:\n  script:\n    - {command}\n"}
    block = scan_ci_checks(_write(tmp_path, {**ci, **files}))
    return block["lint_runs"], block["lint_basis"]


@pytest.mark.parametrize(("command", "files"), [
    pytest.param("tox -e lint", {"tox.ini": TOX_LINT}, id="tox-ini"),
    pytest.param("tox run -e py,lint", {"tox.ini": TOX_LINT}, id="tox-run-two-envs"),
    pytest.param("uv run tox -elint", {"tox.ini": TOX_LINT}, id="uv-run-tox"),
    pytest.param("python -m tox --env=lint", {"tox.ini": TOX_LINT}, id="python-m-tox"),
    pytest.param("tox -e lint", {"tox.toml": '[env.lint]\ncommands = [["ruff", "check", "."]]\n'},
                 id="tox-toml"),
    pytest.param("tox -e type", {"tox.ini": "[testenv:type]\ncommands = mypy src\n"}, id="type-check"),
    pytest.param("nox -s lint", {"noxfile.py": NOX_LINT}, id="nox"),
    pytest.param("nox --sessions tests lint", {"noxfile.py": NOX_LINT}, id="nox-two-sessions"),
    pytest.param("nox -s style", {"noxfile.py": NOX_LINT.replace("@nox.session(python='3.12')",
                                                                 "@nox.session(name='style')")},
                 id="nox-named"),
])
def test_an_env_that_runs_a_linter_reads_as_a_lint_run(tmp_path: Path, command: str,
                                                        files: dict[str, str]) -> None:
    assert _lint(tmp_path, command, files) == (True, "lint_command")


@pytest.mark.parametrize(("command", "files"), [
    pytest.param("tox -e lint", {"tox.ini": "[testenv:lint]\ncommands = - ruff check .\n"}, id="tox-ignored-exit"),
    pytest.param("tox -e lint", {"tox.ini": TOX_LINT + "ignore_outcome = true\n"}, id="tox-ignore-outcome"),
    pytest.param("tox -e lint", {"tox.ini": "[testenv]\ncommands = ruff check .\n[testenv:lint]\ndeps = ruff\n"},
                 id="tox-inherited-commands"),
    pytest.param("tox -e lint", {"tox.ini": TOX_LINT, "sub/tox.ini": TOX_LINT}, id="tox-two-configs"),
    pytest.param("tox -e lint", {"tox.ini": TOX_LINT, "pyproject.toml": "[tool.tox]\n"}, id="tox-pyproject-too"),
    pytest.param("tox -e lint", {"tox.ini": "[testenv:lint]\ncommands = lint: ruff check .\n"}, id="tox-factor"),
    pytest.param("tox -e lint", {"tox.ini": "[testenv:lint]\ncommands = {[base]commands}\n"},
                 id="tox-substitution"),
    pytest.param("tox -e lint -- --fix", {"tox.ini": TOX_LINT}, id="tox-posargs"),
    pytest.param("tox -c other.ini -e lint", {"tox.ini": TOX_LINT}, id="tox-other-config"),
    pytest.param("tox -e missing", {"tox.ini": TOX_LINT}, id="tox-unknown-env"),
    pytest.param("tox -e lint", {"tox.ini": "[testenv:lint\n"}, id="tox-unparsable"),
    pytest.param("nox -s lint", {"noxfile.py": NOX_LINT.replace("*session.posargs)", "success_codes=[0, 1])")},
                 id="nox-success-codes"),
    pytest.param("nox -s lint", {"noxfile.py": NOX_LINT.replace("    session.run(", "    if session.posargs:\n"
                                                                "        session.run(")}, id="nox-conditional"),
    pytest.param("nox -s lint", {"noxfile.py": NOX_LINT.replace("'.'", "PATH")}, id="nox-computed-arg"),
    pytest.param("nox -s lint-3.12", {"noxfile.py": NOX_LINT}, id="nox-parametrized-name"),
    pytest.param("nox -t ci", {"noxfile.py": NOX_LINT}, id="nox-tag"),
    pytest.param("nox -s lint", {"noxfile.py": "def broken(:\n"}, id="nox-syntax-error"),
    pytest.param("nox -s lint", {"noxfile.py": NOX_LINT, "pkg/noxfile.py": NOX_LINT}, id="nox-two-files"),
])
def test_an_env_not_read_whole_stays_not_measured(tmp_path: Path, command: str,
                                                  files: dict[str, str]) -> None:
    assert _lint(tmp_path, command, files) == (None, "check_target")


def test_an_env_running_tox_again_is_not_followed(tmp_path: Path) -> None:
    files = {"tox.ini": "[testenv:lint]\ncommands = tox -e lint\n"}
    assert _lint(tmp_path, "tox -e lint", files) == (None, "check_target")


def test_outside_a_scan_nothing_is_read(tmp_path: Path) -> None:
    _write(tmp_path, {"tox.ini": TOX_LINT})
    assert env_commands(["tox", "-e", "lint"]) is None
    assert is_lint_run("tox -e lint") is False

    @reads_runner_envs
    def scan(repo_root: Path) -> tuple[tuple[str, ...] | None, bool]:
        return env_commands(["tox", "-e", "lint"]), is_lint_run("tox -e lint")

    assert scan(tmp_path) == (("ruff check .",), True)
    assert scan(repo_root=tmp_path) == (("ruff check .",), True)


def test_read_envs_lists_what_it_read(tmp_path: Path) -> None:
    _write(tmp_path, {"tox.ini": TOX_LINT + "\n[testenv:{a,b}]\ncommands = pytest\n", "noxfile.py": NOX_LINT})
    envs = read_envs(tmp_path)
    assert envs.tox == {"lint": ("ruff check .",)}
    assert envs.nox == {"lint": ("ruff check .",)}


@pytest.mark.parametrize("files", [
    pytest.param({"tox.ini": "[testenv]\nignore_outcome = true\n[testenv:lint]\ncommands = ruff check .\n"},
                 id="ini-base-ignore-outcome"),
    pytest.param({"tox.toml": '[env_run_base]\nignore_outcome = true\n[env.lint]\ncommands = [["ruff", "check"]]\n'},
                 id="toml-run-base-ignore-outcome"),
    pytest.param({"tox.ini": "[testenv:lint]\nbase = other\ncommands = ruff check .\n"}, id="ini-base-key"),
])
def test_an_inherited_ignore_outcome_is_not_credited(tmp_path: Path, files: dict[str, str]) -> None:
    assert _lint(tmp_path, "tox -e lint", files) == (None, "check_target")
    # Mirror: the environment sets it back to false.
    if "tox.ini" in files and "ignore_outcome" in files["tox.ini"]:
        mirror = {"tox.ini": files["tox.ini"] + "ignore_outcome = false\n"}
        assert _lint(tmp_path / "mirror", "tox -e lint", mirror) == (True, "lint_command")
