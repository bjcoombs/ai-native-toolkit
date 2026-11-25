"""The ``.`` builtin runs a script like ``source`` (#1033), and ``ci`` setup names run no check.

``. ./test.sh`` read as no check at all (a false 0) while ``source ./test.sh`` read as a
check: ``.`` is now modelled wherever ``source`` is (``check_command``, ``command_resolver``
and ``ci_scripts.script_path``). A sourced script the tree does not hold reads
``script_not_found``, as ``./x`` does (#937), and one sourced to set up the environment
(``. venv/bin/activate``, ``source .env``, ``. ~/.nvm/nvm.sh``) runs no check. A target
whose name pairs ``ci`` with a setup or build word (``make deploy-ci``) reads false.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.check_command import check_verdict
from lib.ci.ci_checks import scan_ci_checks
from lib.ci_scripts import script_path
from lib.dot_source import dot_as_source
from lib.named_check import setup_ci_target

Reading = tuple[bool | None, str, bool | None, str]


def _scan(tmp_path: Path, run: str, files: dict[str, str] | None = None) -> Reading:
    wf = tmp_path / ".github/workflows/ci.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: {run}\n",
                  encoding="utf-8")
    for rel, text in (files or {}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True,
                       encoding="utf-8", errors="replace")
    b = scan_ci_checks(tmp_path)
    return b["enforced_check_runs"], b["enforced_basis"], b["lint_runs"], b["lint_basis"]


@pytest.mark.parametrize(("dot", "source"), [
    (". ./test.sh", "source ./test.sh"), (". ./ci.sh", "source ./ci.sh"),
    (". ./scripts/foo.sh", "source ./scripts/foo.sh"), (". ./lint.sh", "source ./lint.sh"),
    (". venv/bin/activate", "source venv/bin/activate"),
])
def test_dot_reads_as_source(dot: str, source: str) -> None:
    with dot_as_source():
        assert check_verdict(dot) is check_verdict(source)
        assert script_path(dot) == script_path(source)
    assert check_verdict(dot) is False   # outside a CI line (instruction files): as on main


@pytest.mark.parametrize(("run", "files", "expected"), [
    (". ./test.sh", {"test.sh": "pytest\n"}, (True, "enforced_check", None, "check_target")),
    (". ./ci.sh", {"ci.sh": "make build\n"}, (None, "check_target", None, "check_target")),
    (". ./scripts/foo.sh", {"scripts/foo.sh": "x\n"}, (None, "check_target", None, "check_target")),
    (". ./lint.sh", {"lint.sh": "ruff check .\n"}, (True, "enforced_check", True, "lint_command")),
    # missing: the #937 reading, for source as for ./x (source was credited by name before)
    (". ./test.sh", {}, (None, "script_not_found", None, "check_target")),
    ("source ./test.sh", {}, (None, "script_not_found", None, "check_target")),
    # masked: still false
    (". ./test.sh || true", {"test.sh": "pytest\n"}, (False, "not_enforced", False, "no_lint_command")),
])
def test_a_sourced_script_in_ci(tmp_path: Path, run: str, files: dict[str, str],
                                expected: Reading) -> None:
    assert _scan(tmp_path, run, files) == expected


@pytest.mark.parametrize(("run", "files"), [
    (". venv/bin/activate", {}), ("source .venv/bin/activate", {}), ("source .env", {".env": "X=1\n"}),
    (". ./env.sh", {"env.sh": "X=1\n"}), (". ./setup-env.sh", {"setup-env.sh": "X=1\n"}),
    (". ~/.nvm/nvm.sh", {}), ('. "$HOME/.cargo/env"', {}), ("source /opt/ros/humble/setup.bash", {}),
    (".", {}),
])
def test_sourcing_an_environment_runs_no_check(tmp_path: Path, run: str, files: dict[str, str]) -> None:
    assert _scan(tmp_path, run, files) == (False, "no_check_command", False, "no_lint_command")


@pytest.mark.parametrize("run", ["make env", "npm run env", "make activate",
                                 "npx env-cmd -f .env.test jest"])
def test_the_env_rule_is_only_for_a_sourced_file(tmp_path: Path, run: str) -> None:
    """A make target or package script named ``env`` is unread, as on main: null, not false."""
    assert _scan(tmp_path, run)[:2] == (None, "check_target")


@pytest.mark.parametrize(("run", "files"), [
    ("make deploy-ci", {}), ("npm run build:ci", {}), ("make ci-deploy", {}), ("npm run ci:build", {}),
    ("./scripts/deploy-ci.sh", {"scripts/deploy-ci.sh": "echo deploy\n"}),
])
def test_a_ci_setup_name_runs_no_check(tmp_path: Path, run: str, files: dict[str, str]) -> None:
    """The last #1027 thread: these were credited on main and read null after #951."""
    assert setup_ci_target(run)
    assert _scan(tmp_path, run, files) == (False, "no_check_command", False, "no_lint_command")


@pytest.mark.parametrize("run", ["make foo deploy-ci", "make foo CI_BUILD=1", "make ci",
                                 "./ci.sh", "pytest --junitxml=ci-build.xml"])
def test_only_an_all_setup_ci_name_is_one(run: str) -> None:
    assert not setup_ci_target(run)


def test_a_masked_ci_target_reads_not_enforced(tmp_path: Path) -> None:
    """The masked-basis nit from the #1027 re-check: already ``not_enforced`` on main."""
    assert _scan(tmp_path, "make ci || true")[:2] == (False, "not_enforced")
