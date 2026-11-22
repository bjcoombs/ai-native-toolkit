"""Tests for the mutmut 3 adapter and the mutmut version probe.

Moved out of ``test_test_pressure.py`` with the adapter's split into
``lib/test_pressure/mutmut3.py``; the per-package layout tests live in
``test_mutmut3_layout.py``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import lib.test_pressure as tp
from lib.test_pressure import mutation, mutmut3
from lib.test_pressure import (
    compute_survivor_density,
    detect_mutation_config,
    run_bounded_mutation,
)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
# mutmut 3.x adapter (#413)
# ════════════════════════════════════════════════════════════════════════════

_MUTMUT3_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "mutmut/configuration.py", line 90, in _guess_source_paths\n'
    "FileNotFoundError: Could not figure out where the code to mutate is.\n"
)


def _launcher(tmp_path: Path, shebang: str) -> str:
    exe = tmp_path / "bin" / "mutmut"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f"{shebang}\nimport sys\n", encoding="utf-8")
    return str(exe)


def _as_mutmut3(monkeypatch) -> None:
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)
    monkeypatch.setattr(mutation, "_mutmut_major", lambda _exe: 3)
    # the PATH runner: these tests fake ``mutmut run`` itself, so a uv or
    # package-venv invocation (``_resolve_runner``) is tested on its own
    monkeypatch.setattr(mutmut3, "_resolve_runner", lambda *_a: ("path", ("mutmut",)))


def _fake_mutmut3(meta: dict | None, seen: dict, *, returncode: int = 0,
                  stderr: str = ""):
    """A subprocess.run stand-in: git reports "not a repository" (so the copy
    falls back to a tree copy) and ``mutmut run`` writes ``meta`` the way
    mutmut 3 does, recording where it ran and the config it was given."""
    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        assert cmd[:2] == ["mutmut", "run"]
        cwd = Path(kwargs["cwd"])
        seen["cwd"] = cwd
        cfg = cwd / "setup.cfg"
        seen["setup_cfg"] = cfg.read_text(encoding="utf-8") if cfg.exists() else None
        seen["copied"] = (cwd / "pkg" / "calc.py").is_file()
        if meta is not None:
            _write(cwd, "mutants/pkg/calc.py.meta", json.dumps(meta))
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)
    return fake_run


def test_mutmut_major_reads_launcher_interpreter(tmp_path: Path, monkeypatch) -> None:
    exe = _launcher(tmp_path, "#!/opt/tools/mutmut/bin/python")
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="3.6.0\n", stderr="")

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    assert mutation._mutmut_major(exe) == 3
    assert calls[0][0] == "/opt/tools/mutmut/bin/python"


def test_mutmut_major_unknown_degrades_to_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        tp.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
    assert mutation._mutmut_major(None) is None
    assert mutation._mutmut_major(str(tmp_path / "absent")) is None
    assert mutation._mutmut_major(_launcher(tmp_path, "#!/bin/sh")) is None
    assert mutation._mutmut_major(_launcher(tmp_path, "#!/usr/bin/python3")) is None


def test_mutmut3_config_scopes_to_top_level_dirs_and_files() -> None:
    cfg = mutmut3._mutmut3_config(["src/pkg/a.py", "src/pkg/b.py", "app.py"])
    assert cfg == ("[mutmut]\nsource_paths=\n    src\n    app.py\n"
                   "paths_to_mutate=\n    src\n    app.py\n"
                   "only_mutate=\n    src/pkg/a.py\n    src/pkg/b.py\n    app.py\n")


def test_parse_mutmut3_meta_counts_by_exit_code(tmp_path: Path) -> None:
    codes = {"k1": 1, "k2": 36, "s1": 0, "s2": 33, "unchecked": None, "skipped": 34}
    _write(tmp_path, "mutants/pkg/calc.py.meta",
           json.dumps({"exit_code_by_key": codes}))
    _write(tmp_path, "mutants/pkg/empty.py.meta", json.dumps({"exit_code_by_key": {}}))
    _write(tmp_path, "mutants/pkg/bad.py.meta", "{not json")
    assert mutmut3._parse_mutmut3_meta(tmp_path / "mutants") == [
        {"file": "pkg/calc.py", "killed": 2, "survived": 2, "total": 4}]
    assert mutmut3._parse_mutmut3_meta(tmp_path / "absent") == []


def test_run_bounded_mutation_mutmut3_runs_in_scratch_copy(
        tmp_path: Path, monkeypatch) -> None:
    """mutmut 3 with no mutmut config: the pass runs in a copy carrying a
    generated [mutmut] section, reads the .meta results, and leaves the
    assessed tree exactly as it found it."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "setup.cfg", "[metadata]\nname = demo\n")
    _as_mutmut3(monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut3(
        {"exit_code_by_key": {"a": 1, "b": 0, "c": 0}}, seen))
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)

    assert r == {"available": True, "tool": "mutmut", "scope": ["pkg/calc.py"],
                 "mutation_run": True,
                 "groups": [{"root": ".", "config": "generated",
                             "scope": ["pkg/calc.py"], "runner": "path",
                             "mutation_run": True}],
                 "per_file": [{"file": "pkg/calc.py", "killed": 1,
                               "survived": 2, "total": 3}]}
    assert compute_survivor_density(r["per_file"])["overall"] == 2 / 3
    assert seen["cwd"] != tmp_path and seen["copied"]
    assert seen["setup_cfg"].startswith("[metadata]\nname = demo\n")
    assert ("source_paths=\n    pkg\npaths_to_mutate=\n    pkg\n"
            "only_mutate=\n    pkg/calc.py\n") in seen["setup_cfg"]
    assert not seen["cwd"].exists()
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert after == before
    assert (tmp_path / "setup.cfg").read_text(encoding="utf-8") == "[metadata]\nname = demo\n"


def test_run_bounded_mutation_mutmut3_keeps_existing_config(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "pyproject.toml", "[tool.mutmut]\nsource_paths = ['pkg']\n")
    _as_mutmut3(monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut3(
        {"exit_code_by_key": {"a": 1}}, seen))
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is True
    assert seen["setup_cfg"] is None


def test_run_bounded_mutation_mutmut3_failure_names_the_cause(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut3(
        None, {}, returncode=1, stderr=_MUTMUT3_TRACEBACK))
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is False
    assert r["per_file"] == []
    assert r["reason"] == (
        "no mutant records recovered from mutmut output (exit code 1): "
        "FileNotFoundError: Could not figure out where the code to mutate is.")


def test_run_bounded_mutation_mutmut3_timeout_degrades(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        raise subprocess.TimeoutExpired(cmd, tp.MUTATION_TIMEOUT)

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is False
    assert "timeout" in r["reason"]


def test_run_bounded_mutation_mutmut3_timeout_keeps_saved_verdicts(
        tmp_path: Path, monkeypatch) -> None:
    """mutmut 3 saves each verdict to its .meta as it lands, so a run stopped
    at the budget reports the mutants it tested (null ones are untested) and
    says the figures are partial, instead of discarding them."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)
    meta = {"exit_code_by_key": {"k1": 1, "k2": 1, "s1": 0, "u1": None, "u2": None}}
    inner = _fake_mutmut3(meta, {})

    def fake_run(cmd, **kwargs):
        out = inner(cmd, **kwargs)
        if cmd[0] == "mutmut":
            raise subprocess.TimeoutExpired(cmd, tp.MUTATION_TIMEOUT)
        return out

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is True
    assert r["per_file"] == [
        {"file": "pkg/calc.py", "killed": 2, "survived": 1, "total": 3}]
    group = r["groups"][0]
    assert group["mutation_run"] is True and group["partial"] is True
    assert group["reason"].startswith(f"stopped at the {tp.MUTATION_TIMEOUT}s budget")


def test_run_bounded_mutation_mutmut3_timeout_without_focus_verdicts(
        tmp_path: Path, monkeypatch) -> None:
    """A stopped run whose saved verdicts are all for files outside the focus
    set is a plain timeout, not "none of them in the focus set"."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        _write(Path(kwargs["cwd"]), "mutants/pkg/other.py.meta",
               json.dumps({"exit_code_by_key": {"k1": 1}}))
        raise subprocess.TimeoutExpired(cmd, tp.MUTATION_TIMEOUT)

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is False
    assert r["per_file"] == []
    assert r["groups"][0]["reason"] == f"exceeded {tp.MUTATION_TIMEOUT}s timeout"
    assert "partial" not in r["groups"][0]


def test_run_bounded_mutation_mutmut2_keeps_the_legacy_path(
        tmp_path: Path, monkeypatch) -> None:
    """mutmut 2 still runs in place and is read from stdout/junitxml; a failed
    legacy run now names its cause too."""
    _write(tmp_path, "app.py", "def f(): pass")
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)
    monkeypatch.setattr(mutation, "_mutmut_major", lambda _exe: 2)
    cwds: list = []

    def fake_run(cmd, **kwargs):
        cwds.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="Error: boom\n")

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["app.py"], opt_in=True)
    assert set(cwds) == {str(tmp_path)}
    assert r["reason"] == ("no mutant records recovered from mutmut output "
                           "(exit code 2): Error: boom")


def test_run_bounded_mutation_mutmut3_drops_out_of_scope_files(
        tmp_path: Path, monkeypatch) -> None:
    """mutmut 3.0-3.5 ignores ``only_mutate`` and mutates every file under
    ``paths_to_mutate``; results for files outside the scope are dropped."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "pkg/other.py", "def twice(a):\n    return a * 2\n")
    _as_mutmut3(monkeypatch)
    inner = _fake_mutmut3({"exit_code_by_key": {"a": 1, "b": 0}}, {})

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["mutmut", "run"]:
            _write(Path(kwargs["cwd"]), "mutants/pkg/other.py.meta",
                   json.dumps({"exit_code_by_key": {"x": 33, "y": 33}}))
        return inner(cmd, **kwargs)

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["per_file"] == [
        {"file": "pkg/calc.py", "killed": 1, "survived": 1, "total": 2}]


def test_launcher_interpreter_reads_the_long_path_trampoline(tmp_path: Path) -> None:
    """pip and uv write an sh trampoline when the interpreter path exceeds the
    shebang limit; a launcher naming no interpreter falls back to the python
    beside it."""
    forms = {
        "uv": ("'/very/long/env/bin/python'", "/very/long/env/bin/python"),
        "pip": ("/very/long/env/bin/python", "/very/long/env/bin/python"),
        "pip-space": ('"/long env/bin/python"', "/long env/bin/python"),
    }
    for name, (written, expected) in forms.items():
        trampoline = f"#!/bin/sh\n'''exec' {written} \"$0\" \"$@\"\n' '''"
        exe = _launcher(tmp_path / name, trampoline)
        assert mutation._launcher_interpreter(exe) == expected
    plain = _launcher(tmp_path / "b", "#!/bin/sh")
    assert mutation._launcher_interpreter(plain) is None
    sibling = Path(plain).parent / "python"
    sibling.write_text("", encoding="utf-8")
    assert mutation._launcher_interpreter(plain) == str(sibling.resolve())


def test_run_bounded_mutation_mutmut3_ci_mention_is_not_config(
        tmp_path: Path, monkeypatch) -> None:
    """A workflow that names mutmut, or a config file mutmut 3 cannot see (a
    nested pyproject.toml), is not configuration: the generated section and the
    scope filter still apply."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  m:\n    steps:\n      - run: mutmut run --paths-to-mutate=pkg\n")
    _write(tmp_path, "sub/pyproject.toml", "[tool.mutmut]\nsource_paths = ['x']\n")
    assert "mutmut" in detect_mutation_config(tmp_path)["tools"]
    _as_mutmut3(monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut3(
        {"exit_code_by_key": {"a": 1}}, seen))
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is True
    assert "only_mutate=\n    pkg/calc.py\n" in seen["setup_cfg"]


def test_run_bounded_mutation_mutmut3_repo_config_still_reports_scope_only(
        tmp_path: Path, monkeypatch) -> None:
    """Under the repo's own mutmut config the run may cover more files; only
    the focus files are reported, so ``scope`` describes the figures."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "setup.cfg", "[mutmut]\nsource_paths=pkg\n")
    _as_mutmut3(monkeypatch)
    seen: dict = {}
    inner = _fake_mutmut3({"exit_code_by_key": {"a": 1, "b": 0}}, seen)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["mutmut", "run"]:
            _write(Path(kwargs["cwd"]), "mutants/pkg/other.py.meta",
                   json.dumps({"exit_code_by_key": {"x": 0, "y": 0, "z": 0}}))
        return inner(cmd, **kwargs)

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert seen["setup_cfg"] == "[mutmut]\nsource_paths=pkg\n"
    assert r["scope"] == ["pkg/calc.py"]
    assert [p["file"] for p in r["per_file"]] == ["pkg/calc.py"]


def test_run_mutmut3_without_scope_rebuilds_it_from_results(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut3(
        {"exit_code_by_key": {"a": 1}}, {}))
    r = mutmut3._run_mutmut3(tmp_path, [])
    assert r["scope"] == ["pkg/calc.py"]


@pytest.mark.parametrize("stdout,stderr,expected", [
    ("mutmut, version 3.5.0\n", "", 3),
    ("", _MUTMUT3_TRACEBACK, 3),
    ("", "Usage: mutmut [OPTIONS] COMMAND [ARGS]...\nError: No such option '--version'.\n", 2),
    ("", "something else entirely\n", None),
])
def test_mutmut_major_from_cli(monkeypatch, stdout, stderr, expected) -> None:
    """The three observed answers to ``mutmut --version`` in an empty directory
    (3.0-3.5, 3.6, 2.x) and an unrecognised one."""
    monkeypatch.setattr(
        tp.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr=stderr))
    assert mutation._mutmut_major_from_cli() == expected


def test_unreadable_launcher_running_mutmut3_never_touches_the_tree(
        tmp_path: Path, monkeypatch) -> None:
    """A launcher that names no interpreter (a Windows .exe, a wrapper) used to
    mean "unknown version", which took the in-place mutmut 2 path; with mutmut 3
    behind it that writes ``mutants/`` into the assessed repo. The --version
    probe recognises 3.x, so the run goes to the scratch copy."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/nonexistent/bin/" + t)
    seen: dict = {}
    inner = _fake_mutmut3({"exit_code_by_key": {"a": 1}}, seen)

    def fake_run(cmd, **kwargs):
        if cmd == ["mutmut", "--version"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=_MUTMUT3_TRACEBACK)
        return inner(cmd, **kwargs)

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is True
    assert seen["cwd"] != tmp_path
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before


# One row per code in mutmut 3.6.0's status_by_exit_code (mutmut/__main__.py).
@pytest.mark.parametrize("code,bucket", [
    (1, "killed"), (3, "killed"),                                   # killed
    (24, "killed"), (-24, "killed"), (36, "killed"),                # timeout
    (152, "killed"), (255, "killed"),                               # timeout
    (37, "killed"),                                                 # caught by type check
    (0, "survived"),                                                # survived
    (5, "survived"), (33, "survived"),                              # no tests
    (None, None), (2, None), (34, None), (35, None),                # not checked, interrupted, skipped, suspicious
    (-9, None), (-11, None), (99, None),                            # segfault, unknown
])
def test_parse_mutmut3_meta_exit_code_table(tmp_path: Path, code, bucket) -> None:
    _write(tmp_path, "mutants/pkg/calc.py.meta",
           json.dumps({"exit_code_by_key": {"m": code}}))
    got = mutmut3._parse_mutmut3_meta(tmp_path / "mutants")
    if bucket is None:
        assert got == []
    else:
        assert got == [{"file": "pkg/calc.py", "total": 1,
                        "killed": int(bucket == "killed"),
                        "survived": int(bucket == "survived")}]


def test_run_mutmut3_copy_time_spends_the_timeout_budget(
        tmp_path: Path, monkeypatch) -> None:
    """The copy and the run share MUTATION_TIMEOUT: a slow copy shortens the
    run's timeout, and a copy that uses the whole budget means no run at all."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    clock = {"now": 1000.0}
    monkeypatch.setattr(mutation.time, "monotonic", lambda: clock["now"])
    timeouts: list = []

    def fake_run(cmd, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        _write(Path(kwargs["cwd"]), "mutants/pkg/calc.py.meta",
               json.dumps({"exit_code_by_key": {"a": 1}}))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tp.subprocess, "run", fake_run)

    def slow_copy(seconds):
        def copy(_src, _dest):
            clock["now"] += seconds
        return copy

    monkeypatch.setattr(mutmut3, "_copy_repo", slow_copy(100))
    assert mutmut3._run_mutmut3(tmp_path, ["pkg/calc.py"])["mutation_run"] is True
    assert timeouts == [tp.MUTATION_TIMEOUT - 100]

    monkeypatch.setattr(mutmut3, "_copy_repo", slow_copy(tp.MUTATION_TIMEOUT + 1))
    r = mutmut3._run_mutmut3(tmp_path, ["pkg/calc.py"])
    assert r["mutation_run"] is False and "timeout" in r["reason"]
    assert len(timeouts) == 1


def test_mutmut3_reads_config_needs_a_real_section_header(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "# [tool.mutmut]\nnote = 'see [tool.mutmut]'\n")
    _write(tmp_path, "setup.cfg", "# [mutmut]\n[metadata]\nname = demo\n")
    assert mutmut3._mutmut3_reads_config(tmp_path) is False
    _write(tmp_path, "setup.cfg", "[metadata]\nname = demo\n\n[mutmut]\nsource_paths=pkg\n")
    assert mutmut3._mutmut3_reads_config(tmp_path) is True
    _write(tmp_path, "setup.cfg", "")
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n\n[tool.mutmut]  # scope\n")
    assert mutmut3._mutmut3_reads_config(tmp_path) is True


def test_run_mutmut3_names_scope_as_the_cause_when_the_filter_empties_results(
        tmp_path: Path, monkeypatch) -> None:
    """mutmut produced records, none for the focus files: the note must not
    blame the tool's output."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "setup.cfg", "[mutmut]\nsource_paths=pkg\n")
    _as_mutmut3(monkeypatch)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        _write(Path(kwargs["cwd"]), "mutants/other/x.py.meta",
               json.dumps({"exit_code_by_key": {"a": 0}}))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is False
    assert r["reason"] == ("mutmut produced mutants for 1 file(s), none of them "
                           "in the focus set")


def test_run_mutmut3_reason_carries_no_scratch_directory(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        seen["cwd"] = kwargs["cwd"]
        err = f"SyntaxError: bad input in {kwargs['cwd']}/pkg/calc.py\n"
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=err)

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["reason"].endswith(": SyntaxError: bad input in pkg/calc.py")
    assert "assess-mutmut-" not in r["reason"]


def test_no_records_reason_strips_the_longer_scratch_spelling_first(
        tmp_path: Path) -> None:
    """Behind a symlinked temp dir the unresolved scratch path is a substring of
    the resolved one; whichever spelling the tool printed, the stored detail is
    the repo-relative path, and the cut to length happens after the rewrite."""
    real = tmp_path / "private" / "var" / "scratch" / "repo"
    real.mkdir(parents=True)
    (tmp_path / "var").symlink_to(tmp_path / "private" / "var")
    scratch = tmp_path / "var" / "scratch" / "repo"
    assert str(scratch) != str(scratch.resolve())

    def reason(line: str) -> str:
        proc = subprocess.CompletedProcess(["mutmut", "run"], 1, stdout="", stderr=line)
        return mutmut3._no_records_reason("mutmut", proc, scratch=scratch)

    for spelling in (scratch, scratch.resolve()):
        assert reason(f"SyntaxError: bad input in {spelling}/pkg/calc.py\n").endswith(
            ": SyntaxError: bad input in pkg/calc.py")
    long_line = "E" * 290 + f" {scratch.resolve()}/pkg/calc.py\n"
    got = reason(long_line)
    assert "scratch" not in got
    assert len(got.split(": ", 1)[1]) <= mutmut3._MAX_REASON_DETAIL


def test_run_mutmut3_survives_a_scratch_tree_it_cannot_remove(
        tmp_path: Path, monkeypatch) -> None:
    """A read-only directory left in the scratch copy must not raise out of the
    pass: run_bounded_mutation promises never to."""
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)
    locked: list[Path] = []

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        cwd = Path(kwargs["cwd"])
        _write(cwd, "mutants/pkg/calc.py.meta", json.dumps({"exit_code_by_key": {"a": 1}}))
        _write(cwd, "locked/file.txt", "x")
        (cwd / "locked").chmod(0o555)
        locked.append(cwd / "locked")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    try:
        r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    finally:
        for d in locked:
            if d.exists():
                d.chmod(0o755)
                tp.shutil.rmtree(d.parents[1], ignore_errors=True)
    assert r["mutation_run"] is True
