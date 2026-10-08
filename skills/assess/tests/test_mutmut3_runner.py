"""The environment the mutmut 3 pass runs in (``mutmut3._resolve_runner``).

mutmut runs the assessed package's suite, so it must run where the suite's
dependencies are importable: a package virtualenv that already holds mutmut 3,
else a uv-built scratch environment, else the ``mutmut`` on PATH. Also covers
the stored reason naming the first import error or failing test when mutmut's
baseline run fails.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import lib.test_pressure as tp
from lib.test_pressure import mutation, mutmut3


def _write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


_PROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'
_FAR = 1e12  # a deadline that never arrives


def _probe_returns(version: str, calls: list):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{version}\n", stderr="")
    return fake_run


# ── resolution order ─────────────────────────────────────────────────────────

def test_package_venv_with_mutmut3_wins(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pyproject.toml", _PROJECT)
    _write(tmp_path, ".venv/bin/python")
    calls: list = []
    monkeypatch.setattr(mutmut3.subprocess, "run", _probe_returns("3.8.0", calls))
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: "/usr/bin/" + t)

    runner, mutmut = mutmut3._resolve_runner(tmp_path, _FAR)

    python = str(tmp_path / ".venv" / "bin" / "python")
    assert (runner, mutmut) == ("venv", (python, "-m", "mutmut"))
    assert calls[0][0] == python and "version('mutmut')" in calls[0][2]
    assert mutmut3._mutmut_command(tmp_path, "generated", ["calc.py"], mutmut) == [
        python, "-m", "mutmut", "run"]


def test_venv_with_mutmut2_falls_through_to_uv(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pyproject.toml", _PROJECT)
    _write(tmp_path, "venv/bin/python")
    monkeypatch.setattr(mutmut3.subprocess, "run", _probe_returns("2.5.1", []))
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: "/opt/bin/" + t)

    runner, mutmut = mutmut3._resolve_runner(tmp_path, _FAR)

    assert (runner, mutmut) == ("uv", (
        "/opt/bin/uv", "run", "--quiet", "--project", ".",
        "--with", "mutmut==3.8.0", "--with", "pytest", "mutmut"))


def test_venv_without_mutmut_falls_through(tmp_path: Path, monkeypatch) -> None:
    """The probe fails (mutmut not installed there): nothing is installed into
    the package's own environment, the next runner is used."""
    _write(tmp_path, ".venv/bin/python")
    _write(tmp_path, "setup.cfg", "[metadata]\nname = demo\n")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="",
                                           stderr="PackageNotFoundError: mutmut")
    monkeypatch.setattr(mutmut3.subprocess, "run", fake_run)
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: "/opt/bin/" + t)

    assert mutmut3._resolve_runner(tmp_path, _FAR) == ("path", ("mutmut",))


def test_uv_needs_a_project_table(tmp_path: Path, monkeypatch) -> None:
    """A pyproject.toml holding only tool config gives uv nothing to build."""
    _write(tmp_path, "pyproject.toml", "[tool.mutmut]\nsource_paths = ['src']\n")
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: "/opt/bin/" + t)
    assert mutmut3._resolve_runner(tmp_path, _FAR) == ("path", ("mutmut",))

    _write(tmp_path, "pyproject.toml", "not = [valid")
    assert mutmut3._resolve_runner(tmp_path, _FAR) == ("path", ("mutmut",))


def test_no_uv_on_path_uses_path_mutmut(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pyproject.toml", _PROJECT)
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: None)
    assert mutmut3._resolve_runner(tmp_path, _FAR) == ("path", ("mutmut",))


def test_venv_probe_respects_the_deadline(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, ".venv/bin/python")
    calls: list = []
    monkeypatch.setattr(mutmut3.subprocess, "run", _probe_returns("3.8.0", calls))
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: None)

    assert mutmut3._resolve_runner(tmp_path, time.monotonic() - 1) == (
        "path", ("mutmut",))
    assert calls == []


def test_uv_runner_command_keeps_the_mutant_globs(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml",
           _PROJECT + "[tool.mutmut]\nsource_paths = ['lib']\n")
    _write(tmp_path, "lib/calc.py", "x = 1\n")
    uv = ("uv", "run", "--quiet", "--project", ".", "--with", "mutmut==3.8.0",
          "--with", "pytest", "mutmut")
    assert mutmut3._mutmut_command(tmp_path, "repo", ["lib/calc.py"], uv) == [
        *uv, "run", "lib.calc.*"]


# ── the uv environment stays in the scratch directory ───────────────────────

def test_runner_env_puts_the_uv_environment_in_scratch(tmp_path: Path,
                                                      monkeypatch) -> None:
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/home/user/.venvs/mine")
    monkeypatch.setenv("VIRTUAL_ENV", "/home/user/.venvs/active")
    env = mutmut3._runner_env("uv", tmp_path)
    assert env is not None
    assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / "venv")
    assert "VIRTUAL_ENV" not in env
    assert mutmut3._runner_env("path", tmp_path) is None
    assert mutmut3._runner_env("venv", tmp_path) is None


def test_run_group_records_the_runner_and_scratch_env(tmp_path: Path,
                                                     monkeypatch) -> None:
    _write(tmp_path, "pkg/pyproject.toml", _PROJECT)
    _write(tmp_path, "pkg/calc.py", "def f():\n    return 1\n")
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: "/opt/bin/" + t)
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        seen["cmd"], seen["env"], seen["cwd"] = cmd, kwargs.get("env"), kwargs["cwd"]
        _write(Path(kwargs["cwd"]), "mutants/calc.py.meta",
               json.dumps({"exit_code_by_key": {"calc.x_f__mutmut_1": 1}}))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(mutmut3.subprocess, "run", fake_run)

    record, rows = mutmut3._run_group(tmp_path, "pkg", ["calc.py"],
                                      time.monotonic() + 60)

    assert record["runner"] == "uv" and record["mutation_run"] is True
    assert rows == [{"file": "pkg/calc.py", "killed": 1, "survived": 0, "total": 1}]
    assert seen["cmd"][:2] == ["/opt/bin/uv", "run"]
    scratch = Path(seen["cwd"]).parents[1]
    assert Path(seen["env"]["UV_PROJECT_ENVIRONMENT"]).parent == scratch
    assert not (tmp_path / "pkg" / ".venv").exists()


def test_run_bounded_mutation_reports_the_runner(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "setup.cfg", "[metadata]\nname = demo\n")
    _write(tmp_path, "pkg/calc.py", "x = 1\n")
    monkeypatch.setattr(tp.shutil, "which", lambda t: None if t == "uv" else "/b/" + t)
    monkeypatch.setattr(mutation, "_mutmut_major", lambda _exe: 3)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout=_STATS_FAILED_OUTPUT,
                                           stderr="")
    monkeypatch.setattr(tp.subprocess, "run", fake_run)

    r = tp.run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)

    assert r["groups"][0]["runner"] == "path"
    assert "first failure: ModuleNotFoundError: No module named 'networkx'" in r["reason"]


# ── the stored reason says why the baseline run failed ──────────────────────

_STATS_FAILED_OUTPUT = (
    "⠋ Generating mutants\n"
    "==================================== ERRORS ====================================\n"
    "____________________ ERROR collecting tests/test_graph.py ______________________\n"
    "ImportError while importing test module '/tmp/x/repo/mutants/tests/test_graph.py'.\n"
    "tests/test_graph.py:3: in <module>\n"
    "    import networkx\n"
    "E   ModuleNotFoundError: No module named 'networkx'\n"
    "=========================== short test summary info ============================\n"
    "ERROR tests/test_graph.py\n"
    "failed to collect stats. runner returned 2\n"
)


def test_reason_names_the_missing_module() -> None:
    proc = subprocess.CompletedProcess(["mutmut", "run"], 1,
                                       stdout=_STATS_FAILED_OUTPUT, stderr="")
    assert mutmut3._no_records_reason("mutmut", proc) == (
        "no mutant records recovered from mutmut output (exit code 1): "
        "failed to collect stats. runner returned 2; first failure: "
        "ModuleNotFoundError: No module named 'networkx'")


def test_reason_falls_back_to_the_first_failing_test(tmp_path: Path) -> None:
    """A suite that catches its own ImportError (``networkx not installed``)
    fails an assertion instead; pytest's short summary names the test, and
    the scratch prefix is cut like the rest of the detail."""
    out = (f"  File \"{tmp_path}/repo/mutants/tests/test_doc_graph.py\", line 19\n"
           "AssertionError: assert False is True\n"
           "=========================== short test summary info ============================\n"
           "FAILED tests/test_doc_graph.py::test_empty_repo - Asser...\n"
           "FAILED tests/test_doc_graph.py::test_other - Asser...\n"
           "failed to collect stats. runner returned 1\n")
    proc = subprocess.CompletedProcess(["mutmut", "run"], 1, stdout=out, stderr="")
    assert mutmut3._no_records_reason("mutmut", proc, scratch=tmp_path) == (
        "no mutant records recovered from mutmut output (exit code 1): "
        "failed to collect stats. runner returned 1; first failure: "
        "FAILED tests/test_doc_graph.py::test_empty_repo - Asser...")


def test_reason_does_not_repeat_the_detail() -> None:
    proc = subprocess.CompletedProcess(
        ["mutmut", "run"], 1, stdout="",
        stderr="Traceback:\nModuleNotFoundError: No module named 'grimp'\n")
    assert mutmut3._no_records_reason("mutmut", proc) == (
        "no mutant records recovered from mutmut output (exit code 1): "
        "ModuleNotFoundError: No module named 'grimp'")


def test_reason_cuts_scratch_env_and_package_paths(tmp_path: Path,
                                                   monkeypatch) -> None:
    """An ImportError names the file it imported from: in the uv runner's
    scratch environment, or in the assessed package's own venv. Neither
    absolute path (gone, or a home directory) reaches the stored reason."""
    _write(tmp_path, "pkg/pyproject.toml", _PROJECT)
    _write(tmp_path, "pkg/calc.py", "x = 1\n")
    monkeypatch.setattr(mutmut3.shutil, "which", lambda t: "/opt/bin/" + t)
    pkg_on_disk = tmp_path / "pkg"

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        scratch = Path(kwargs["cwd"]).parents[1]
        out = (f"E   ImportError: cannot import name 'G' from 'nx' "
               f"({scratch}/venv/lib/nx.py)\n"
               f"E   ImportError: from {pkg_on_disk}/.venv/lib/y.py\n"
               "failed to collect stats. runner returned 1\n")
        return subprocess.CompletedProcess(cmd, 1, stdout=out, stderr="")
    monkeypatch.setattr(mutmut3.subprocess, "run", fake_run)

    record, _ = mutmut3._run_group(tmp_path, "pkg", ["calc.py"], time.monotonic() + 60)

    assert record["reason"].endswith(
        "first failure: ImportError: cannot import name 'G' from 'nx' "
        "(<scratch>/venv/lib/nx.py)")
    assert str(tmp_path) not in record["reason"]
    proc = subprocess.CompletedProcess(
        ["m"], 1, stdout="", stderr=f"ImportError: from {pkg_on_disk}/.venv/lib/y.py\n")
    assert mutmut3._no_records_reason(
        "mutmut", proc, labels={pkg_on_disk: "pkg"}).endswith(
        "ImportError: from pkg/.venv/lib/y.py")


def test_clean_exit_adds_no_cause() -> None:
    proc = subprocess.CompletedProcess(["mutmut", "run"], 0,
                                       stdout=_STATS_FAILED_OUTPUT, stderr="")
    assert mutmut3._no_records_reason("mutmut", proc) == (
        "no mutant records recovered from mutmut output (exit code 0)")


# ── integration: a real uv environment ──────────────────────────────────────

@pytest.mark.skipif(shutil.which("uv") is None
                    or os.environ.get("ASSESS_UV_INTEGRATION") != "1",
                    reason="opt-in: set ASSESS_UV_INTEGRATION=1 with uv on PATH")
def test_uv_runner_imports_the_package_dependencies(tmp_path: Path) -> None:
    """A suite importing a dependency the PATH mutmut's environment lacks
    (here a local path dependency) runs under the uv runner, and the assessed
    tree gets no virtualenv or lock file. Opt-in: uv resolves hatchling,
    mutmut and pytest from the package index, and a network hiccup must not
    turn the required suite red."""
    _write(tmp_path, "pyproject.toml",
           '[project]\nname = "demo"\nversion = "0.1.0"\n'
           'requires-python = ">=3.11"\ndependencies = ["helperdep"]\n'
           '[tool.uv.sources]\nhelperdep = { path = "helperdep" }\n'
           '[tool.mutmut]\nsource_paths = ["calc"]\n'
           '[tool.pytest.ini_options]\npythonpath = ["."]\n')
    _write(tmp_path, "helperdep/pyproject.toml",
           '[project]\nname = "helperdep"\nversion = "0.1.0"\n'
           '[build-system]\nrequires = ["hatchling"]\n'
           'build-backend = "hatchling.build"\n')
    _write(tmp_path, "helperdep/helperdep/__init__.py", "ONE = 1\n")
    _write(tmp_path, "calc/__init__.py", "")
    _write(tmp_path, "calc/ops.py",
           "from helperdep import ONE\n\ndef inc(x):\n    return x + ONE\n")
    _write(tmp_path, "tests/test_ops.py",
           "from calc.ops import inc\n\ndef test_inc():\n    assert inc(1) == 2\n")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    record, rows = mutmut3._run_group(tmp_path, "", ["calc/ops.py"],
                                      time.monotonic() + 240)

    assert record["runner"] == "uv", record
    assert record["mutation_run"] is True, record
    assert rows and rows[0]["file"] == "calc/ops.py" and rows[0]["total"] > 0
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert after == before
