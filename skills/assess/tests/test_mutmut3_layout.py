"""The mutmut 3 pass across package layouts: grouping by package root, the
package's own config versus a generated one, files the tests read outside the
package, and several packages in one focus set.

mutmut itself is replaced by ``_fake_mutmut``, which reads its configuration in
the working directory and builds ``mutants/`` the way mutmut 3 does
(``source_paths``, then ``also_copy`` plus its defaults, ``../`` paths
included), then runs a stand-in suite against that tree.
"""
from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path

import lib.test_pressure as tp
from lib import doc_graph, structure_graph
from lib.test_pressure import mutation, mutmut3
from lib.test_pressure import run_bounded_mutation, scan_test_pressure

_STATS_FAILED = "failed to collect stats. runner returned 1"
_DEPRECATION = ("mutmut/configuration.py:143: UserWarning: The config "
                "paths_to_mutate is deprecated. Please rename it to source_paths\n"
                '  warnings.warn("The config paths_to_mutate is deprecated.")\n')


def _write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _as_mutmut3(monkeypatch) -> None:
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)
    monkeypatch.setattr(mutation, "_mutmut_major", lambda _exe: 3)
    # the PATH runner: these tests fake ``mutmut run`` itself, so a uv or
    # package-venv invocation (``_resolve_runner``) is tested on its own
    monkeypatch.setattr(mutmut3, "_resolve_runner", lambda *_a: ("path", ("mutmut",)))


def _mutmut_config(cwd: Path) -> tuple[list[str], list[str]]:
    """``source_paths`` and ``also_copy`` as mutmut 3 reads them in ``cwd``."""
    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        section = tomllib.loads(pyproject.read_text("utf-8")).get("tool", {}).get("mutmut")
        if section is not None:
            return list(section.get("source_paths", [])), list(section.get("also_copy", []))
    parser = configparser.ConfigParser()
    parser.read(cwd / "setup.cfg")

    def get(key: str) -> list[str]:
        return [v for v in parser.get("mutmut", key, fallback="").split("\n") if v]

    return get("source_paths"), get("also_copy")


def _copy_into_mutants(cwd: Path, rel: str) -> None:
    src = cwd / rel
    dest = Path(os.path.normpath(cwd / "mutants" / rel))
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    elif src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


Suite = Callable[[Path], bool]


def _fake_mutmut(suite: Suite, runs: list[Path], commands: list | None = None):
    """A subprocess.run stand-in: git runs for real; ``mutmut run`` builds
    ``mutants/`` from the config in its cwd, runs ``suite(cwd)`` as the
    baseline, and on a pass records one killed and one surviving mutant per
    source file."""
    real_run = subprocess.run
    commands = [] if commands is None else commands

    def run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        assert cmd[:2] == ["mutmut", "run"]
        cwd = Path(kwargs["cwd"])
        runs.append(cwd)
        commands.append(cmd)
        sources, also_copy = _mutmut_config(cwd)
        for rel in [*sources, *also_copy, "tests", "setup.cfg", "pyproject.toml"]:
            _copy_into_mutants(cwd, rel)
        if not suite(cwd):
            return subprocess.CompletedProcess(
                cmd, 1, stdout=f"{_STATS_FAILED}\n", stderr=_DEPRECATION)
        for source in sources:
            files = [cwd / source] if source.endswith(".py") else (cwd / source).rglob("*.py")
            for f in files:
                rel = f.relative_to(cwd).as_posix()
                key = mutmut3._mutant_glob(rel)[:-1] + "x_f__mutmut_1"
                if cmd[2:] and not any(fnmatch(key, pat) for pat in cmd[2:]):
                    continue  # mutmut checks only the named mutants
                _write(cwd, f"mutants/{rel}.meta",
                       json.dumps({"exit_code_by_key": {"k": 1, "s": 0}}))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=_DEPRECATION)

    return run


def _reads_from_test_dir(*rel_to_climb: str, levels: int = 2) -> Suite:
    """A suite whose test, at ``mutants/tests/test_x.py``, opens a file at
    ``Path(__file__).parents[levels] / rel`` - the way a test reaches a
    repository file outside its package."""
    def suite(cwd: Path) -> bool:
        test_file = cwd / "mutants" / "tests" / "test_x.py"
        return test_file.is_file() and all(
            (test_file.parents[levels] / rel).is_file() for rel in rel_to_climb)
    return suite


def _git_repo(root: Path) -> None:
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                 "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if ".git" not in p.relative_to(root).parts)


# ── tests that read a root file outside the package ──────────────────────────

def test_generated_config_mirrors_root_files_the_tests_read(
        tmp_path: Path, monkeypatch) -> None:
    """The reproduced failure: a package in ``tools/`` whose test opens
    ``.claude-plugin/plugin.json`` and ``skills/x/SKILL.md`` at the repository
    root. Copying only ``source_paths`` loses them; the generated ``also_copy``
    puts each where the test's climb from ``mutants/`` lands."""
    _write(tmp_path, ".claude-plugin/plugin.json", "{}")
    _write(tmp_path, "skills/x/SKILL.md", "# x")
    _write(tmp_path, "tools/pyproject.toml", "[project]\nname = 'tools'\n")
    _write(tmp_path, "tools/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "tools/tests/test_x.py", "")
    _git_repo(tmp_path)
    _as_mutmut3(monkeypatch)
    runs: list[Path] = []
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(
        _reads_from_test_dir(".claude-plugin/plugin.json", "skills/x/SKILL.md"), runs))
    before = _tree(tmp_path)

    r = run_bounded_mutation(tmp_path, hot_files=["tools/calc.py"], opt_in=True)

    assert r["mutation_run"] is True
    assert r["per_file"] == [
        {"file": "tools/calc.py", "killed": 1, "survived": 1, "total": 2}]
    assert r["groups"] == [{"root": "tools", "config": "generated",
                            "scope": ["tools/calc.py"], "runner": "path",
                            "mutation_run": True}]
    assert [run.name for run in runs] == ["tools"]
    assert _tree(tmp_path) == before
    assert not (tmp_path / "tools" / "setup.cfg").exists()


def test_without_the_mirror_the_same_suite_fails_and_names_the_cause(
        tmp_path: Path, monkeypatch) -> None:
    """Control for the test above: the old single global config (source paths
    only) fails the baseline, and the reason is mutmut's own stop message, not
    the deprecation warning printed before it."""
    _write(tmp_path, ".claude-plugin/plugin.json", "{}")
    _write(tmp_path, "tools/pyproject.toml", "[project]\nname = 'tools'\n")
    _write(tmp_path, "tools/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "tools/tests/test_x.py", "")
    _as_mutmut3(monkeypatch)
    monkeypatch.setattr(mutmut3, "_mirror_ancestors", lambda *_a: None)
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(
        _reads_from_test_dir(".claude-plugin/plugin.json"), []))

    r = run_bounded_mutation(tmp_path, hot_files=["tools/calc.py"], opt_in=True)

    assert r["mutation_run"] is False
    assert r["reason"] == ("no mutant records recovered from mutmut output "
                           f"(exit code 1): {_STATS_FAILED}")


def test_root_package_copies_the_rest_of_the_repo_into_mutants(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, ".config/settings.json", "{}")
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "tests/test_x.py", "")
    _as_mutmut3(monkeypatch)
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(
        _reads_from_test_dir(".config/settings.json", levels=1), []))
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is True
    assert r["groups"][0]["root"] == "."


def test_scratch_copy_is_a_git_repository_at_the_copied_tree(
        tmp_path: Path, monkeypatch) -> None:
    """A test that asks git about HEAD finds the copied files there."""
    _write(tmp_path, "FLOOR.md", "floor")
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _git_repo(tmp_path)
    _as_mutmut3(monkeypatch)

    def suite(cwd: Path) -> bool:
        proc = subprocess.run(["git", "show", "HEAD:FLOOR.md"], cwd=cwd,
                              capture_output=True, text=True, check=False)
        return proc.stdout == "floor"

    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(suite, []))
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["mutation_run"] is True
    assert not (tmp_path / "mutants").exists()


# ── a package with its own [tool.mutmut] config ──────────────────────────────

_OWN_CONFIG = ('[tool.mutmut]\nsource_paths = ["src"]\n'
               'only_mutate = ["src/calc.py"]\nalso_copy = ["data"]\n')


def test_subdirectory_config_runs_unchanged_from_its_package(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "sub/pyproject.toml", _OWN_CONFIG)
    _write(tmp_path, "sub/src/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "sub/data/table.csv", "a,b")
    _write(tmp_path, "sub/tests/test_x.py", "")
    _as_mutmut3(monkeypatch)
    runs: list[Path] = []
    commands: list = []
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(
        _reads_from_test_dir("data/table.csv", levels=2), runs, commands))

    r = run_bounded_mutation(tmp_path, hot_files=["sub/src/calc.py"], opt_in=True)

    # narrowed to the focus file's mutants: mutmut strips a leading "src."
    assert commands == [["mutmut", "run", "calc.*"]]
    assert r["per_file"] == [
        {"file": "sub/src/calc.py", "killed": 1, "survived": 1, "total": 2}]
    assert r["groups"][0]["config"] == "repo"
    assert runs[0].name == "sub"
    assert not (runs[0] / "setup.cfg").exists()


def test_own_config_naming_a_missing_source_path_is_reported_not_run(
        tmp_path: Path, monkeypatch) -> None:
    """A ``source_paths`` entry made at run time (here: absent from the
    checkout, as a gitignored ``src`` link is) is named and not recreated."""
    _write(tmp_path, "sub/pyproject.toml", _OWN_CONFIG)
    _write(tmp_path, "sub/scripts/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)
    runs: list[Path] = []
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(lambda _c: True, runs))

    r = run_bounded_mutation(tmp_path, hot_files=["sub/scripts/calc.py"], opt_in=True)

    assert r["mutation_run"] is False
    assert runs == []
    assert r["reason"] == r["groups"][0]["reason"]
    assert r["reason"] == ("the mutmut config in sub/pyproject.toml names "
                           "source_paths missing from a clean copy (src); a path "
                           "made at run time is not recreated")


def test_focus_file_outside_own_config_scope_is_listed_as_unmeasured(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "sub/pyproject.toml", _OWN_CONFIG)
    _write(tmp_path, "sub/src/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "sub/src/other.py", "def f():\n    return 1\n")
    _write(tmp_path, "sub/data/table.csv", "a,b")
    _as_mutmut3(monkeypatch)
    commands: list = []
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(lambda _c: True, [], commands))
    r = run_bounded_mutation(tmp_path, hot_files=["sub/src/calc.py", "sub/src/other.py"],
                             opt_in=True)
    assert commands == [["mutmut", "run", "calc.*"]]
    assert [p["file"] for p in r["per_file"]] == ["sub/src/calc.py"]
    assert r["groups"][0]["unmeasured"] == ["sub/src/other.py"]


def test_focus_file_outside_own_config_scope_is_reported_not_run(
        tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "sub/pyproject.toml", _OWN_CONFIG)
    _write(tmp_path, "sub/src/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "sub/src/other.py", "def f():\n    return 1\n")
    _as_mutmut3(monkeypatch)
    runs: list[Path] = []
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(lambda _c: True, runs))
    r = run_bounded_mutation(tmp_path, hot_files=["sub/src/other.py"], opt_in=True)
    assert runs == []
    assert r["reason"].startswith(
        "no focus file is in the scope of the mutmut config in sub/pyproject.toml")


# ── two packages in one focus set ────────────────────────────────────────────

def _two_packages(root: Path) -> None:
    _write(root, "a/pyproject.toml", "[project]\nname = 'a'\n")
    _write(root, "a/alpha.py", "def f():\n    return 1\n")
    _write(root, "a/tests/test_x.py", "")
    _write(root, "b/pyproject.toml", _OWN_CONFIG)
    _write(root, "b/src/calc.py", "def add(a, b):\n    return a + b\n")
    _write(root, "b/data/table.csv", "a,b")
    _write(root, "b/tests/test_x.py", "")


def test_two_packages_run_separately_and_report_in_one_shape(
        tmp_path: Path, monkeypatch) -> None:
    _two_packages(tmp_path)
    _as_mutmut3(monkeypatch)
    runs: list[Path] = []
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(lambda _c: True, runs))

    r = run_bounded_mutation(tmp_path, hot_files=["a/alpha.py", "b/src/calc.py"],
                             opt_in=True)

    assert r["scope"] == ["a/alpha.py", "b/src/calc.py"]
    assert [p["file"] for p in r["per_file"]] == ["a/alpha.py", "b/src/calc.py"]
    assert [(g["root"], g["config"], g["mutation_run"]) for g in r["groups"]] == [
        ("a", "generated", True), ("b", "repo", True)]
    assert [run.name for run in runs] == ["a", "b"]
    assert runs[0].parent != runs[1].parent  # a fresh scratch copy per package


def test_a_group_that_cannot_run_keeps_its_own_reason(
        tmp_path: Path, monkeypatch) -> None:
    _two_packages(tmp_path)
    _as_mutmut3(monkeypatch)
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(
        lambda cwd: cwd.name == "b", []))

    r = run_bounded_mutation(tmp_path, hot_files=["a/alpha.py", "b/src/calc.py"],
                             opt_in=True)

    assert r["mutation_run"] is True
    assert "reason" not in r
    assert [p["file"] for p in r["per_file"]] == ["b/src/calc.py"]
    assert r["groups"][0]["mutation_run"] is False
    assert r["groups"][0]["reason"].endswith(_STATS_FAILED)


def test_test_pressure_block_carries_the_groups(tmp_path: Path, monkeypatch) -> None:
    _two_packages(tmp_path)
    _as_mutmut3(monkeypatch)
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(lambda cwd: cwd.name == "b", []))
    block = scan_test_pressure(tmp_path, hot_files=["a/alpha.py", "b/src/calc.py"],
                               opt_in=True)
    assert block["mutation_run"] is True
    assert [g["root"] for g in block["mutation_groups"]] == ["a", "b"]
    assert "mutation_note" not in block


def test_when_every_group_fails_the_reason_names_each_root(
        tmp_path: Path, monkeypatch) -> None:
    _two_packages(tmp_path)
    _as_mutmut3(monkeypatch)
    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(lambda _c: False, []))
    r = run_bounded_mutation(tmp_path, hot_files=["a/alpha.py", "b/src/calc.py"],
                             opt_in=True)
    assert r["mutation_run"] is False
    assert r["reason"].startswith("a: no mutant records")
    assert "; b: no mutant records" in r["reason"]


def test_groups_share_one_timeout_budget(tmp_path: Path, monkeypatch) -> None:
    """The first package's run spends the whole budget; the second records a
    timeout without starting mutmut."""
    _two_packages(tmp_path)
    _as_mutmut3(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(mutmut3.time, "monotonic", lambda: clock["now"])
    runs: list[Path] = []
    inner = _fake_mutmut(lambda _c: True, runs)

    def run(cmd, **kwargs):
        out = inner(cmd, **kwargs)
        if cmd[0] == "mutmut":
            clock["now"] += tp.MUTATION_TIMEOUT
        return out

    copies: list[Path] = []
    real_copy = mutmut3._copy_repo
    monkeypatch.setattr(mutmut3, "_copy_repo",
                        lambda src, dest: copies.append(dest) or real_copy(src, dest))
    monkeypatch.setattr(tp.subprocess, "run", run)
    r = run_bounded_mutation(tmp_path, hot_files=["a/alpha.py", "b/src/calc.py"],
                             opt_in=True)
    assert len(runs) == 1
    assert len(copies) == 1  # a spent budget skips the second copy too
    assert r["groups"][1]["reason"] == f"exceeded {tp.MUTATION_TIMEOUT}s timeout"


# ── units ────────────────────────────────────────────────────────────────────

def test_package_root_is_the_nearest_marker(tmp_path: Path) -> None:
    _write(tmp_path, "a/setup.py")
    _write(tmp_path, "a/b/setup.cfg")
    assert mutmut3._package_root(tmp_path, "a/b/c/x.py") == "a/b"
    assert mutmut3._package_root(tmp_path, "a/y.py") == "a"
    assert mutmut3._package_root(tmp_path, "z/y.py") == ""
    assert mutmut3._package_root(tmp_path, "top.py") == ""


def test_ancestors_are_mirrored_one_level_down(tmp_path: Path) -> None:
    for rel in (".git/HEAD", ".assess/x", "README.md", "shared.txt", "docs/a.md",
                "pkgs/shared.txt", "pkgs/other/x", "pkgs/tool/pyproject.toml",
                "pkgs/tool/calc.py", "pkgs/tool/tests/t.py", "pkgs/tool/data.csv",
                "pkgs/tool/README.md", "pkgs/tool/mutants/old"):
        _write(tmp_path, rel, rel)
    assert mutmut3._package_entries(tmp_path / "pkgs" / "tool", ["calc.py"]) == [
        "README.md", "data.csv"]
    mutmut3._mirror_ancestors(tmp_path, "pkgs/tool")
    tool, pkgs = tmp_path / "pkgs" / "tool", tmp_path / "pkgs"
    # the parent (pkgs/) into the package, never the package into itself
    assert (tool / "other" / "x").is_file() and (tool / "shared.txt").is_file()
    assert not (tool / "tool").exists()
    # the root into pkgs/, nothing overwritten, VCS and tool state left out
    assert (pkgs / "docs" / "a.md").is_file()
    assert (pkgs / "shared.txt").read_text() == "pkgs/shared.txt"
    assert (tool / "README.md").read_text() == "pkgs/tool/README.md"
    assert not (pkgs / ".git").exists() and not (pkgs / ".assess").exists()
    # nearest level first: the package holds the parent's entries, not the root's
    assert not (tool / "docs").exists()


def test_scratch_git_snapshot_includes_the_mirrored_tree(
        tmp_path: Path, monkeypatch) -> None:
    """The floor-check shape: a test in a subdirectory package runs ``git
    grep HEAD`` from its climbed repository root and opens what it finds."""
    _write(tmp_path, "skills/m/SKILL.md", "ANCHOR")
    _write(tmp_path, "tools/pyproject.toml", "[project]\nname = 'tools'\n")
    _write(tmp_path, "tools/calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "tools/tests/test_x.py", "")
    _git_repo(tmp_path)
    _as_mutmut3(monkeypatch)

    def suite(cwd: Path) -> bool:
        root = (cwd / "mutants" / "tests" / "test_x.py").parents[2]
        proc = subprocess.run(["git", "grep", "-l", "ANCHOR", "HEAD"], cwd=root,
                              capture_output=True, text=True, check=False)
        found = [line.split(":", 1)[1] for line in proc.stdout.splitlines()]
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                capture_output=True, text=True, check=False).stdout
        clean = all(line.endswith("mutants/") for line in status.splitlines())
        return found == ["skills/m/SKILL.md"] and (root / found[0]).is_file() and clean

    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(suite, []))
    r = run_bounded_mutation(tmp_path, hot_files=["tools/calc.py"], opt_in=True)
    assert r["mutation_run"] is True


def test_root_config_and_suite_never_shadow_a_setup_cfg_package(
        tmp_path: Path, monkeypatch) -> None:
    """A package marked only by setup.py under a root pyproject.toml with its
    own [tool.mutmut]: the mirror must not carry the root config (mutmut reads
    it before setup.cfg) or the root suite into the package."""
    _write(tmp_path, "pyproject.toml", '[tool.mutmut]\nsource_paths = ["lib"]\n')
    _write(tmp_path, "tests/test_root.py", "")
    _write(tmp_path, "lib/x.py", "")
    _write(tmp_path, "pkg/setup.py", "")
    _write(tmp_path, "pkg/calc.py", "def add(a, b):\n    return a + b\n")
    _as_mutmut3(monkeypatch)
    runs: list[Path] = []

    def suite(cwd: Path) -> bool:  # other root entries still mirror
        return (not (cwd / "pyproject.toml").exists() and not (cwd / "tests").exists()
                and (cwd / "lib" / "x.py").is_file())

    monkeypatch.setattr(tp.subprocess, "run", _fake_mutmut(suite, runs))
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/calc.py"], opt_in=True)
    assert r["groups"] == [{"root": "pkg", "config": "generated",
                            "scope": ["pkg/calc.py"], "runner": "path",
                            "mutation_run": True}]
    assert r["per_file"] == [
        {"file": "pkg/calc.py", "killed": 1, "survived": 1, "total": 2}]


def test_tool_error_line_skips_python_warnings() -> None:
    proc = subprocess.CompletedProcess(["mutmut"], 1, stdout=f"x\n{_STATS_FAILED}\n",
                                       stderr=_DEPRECATION)
    assert mutmut3._tool_error_line(proc) == _STATS_FAILED
    proc = subprocess.CompletedProcess(["mutmut"], 1, stdout="",
                                       stderr=_DEPRECATION + "ValueError: boom\n")
    assert mutmut3._tool_error_line(proc) == "ValueError: boom"


def test_read_config_handles_setup_cfg_and_broken_toml(tmp_path: Path) -> None:
    _write(tmp_path, "setup.cfg", "[mutmut]\nsource_paths=\n    lib\n    app.py\n"
                                  "only_mutate=lib/a.py\n")
    cfg = mutmut3._read_mutmut3_config(tmp_path)
    assert cfg["source_paths"] == ["lib", "app.py"]
    assert cfg["only_mutate"] == ["lib/a.py"]
    assert mutmut3._covered_by_config(cfg, "lib/a.py")
    assert not mutmut3._covered_by_config(cfg, "lib/b.py")
    assert not mutmut3._covered_by_config(cfg, "other/a.py")
    _write(tmp_path, "pyproject.toml", "[tool.mutmut]\nsource_paths = [\n")
    assert mutmut3._read_mutmut3_config(tmp_path)["source_paths"] == []
    _write(tmp_path, "setup.cfg", "[mutmut\nbroken")
    _write(tmp_path, "pyproject.toml", "")
    assert mutmut3._read_mutmut3_config(tmp_path)["source_paths"] == []


def test_a_leftover_mutants_tree_is_never_walked() -> None:
    """mutmut 3 leaves ``mutants/`` (copies of sources and tests) after a
    local run; the shared walks skip it so nothing is counted twice."""
    assert "mutants" in doc_graph.EXCLUDE_DIRS
    assert "mutants" in structure_graph.EXCLUDE_DIRS


def test_tool_error_line_keeps_a_raised_warning() -> None:
    """A suite under ``filterwarnings = error`` fails with the warning as its
    exception; that line is the cause, not noise."""
    proc = subprocess.CompletedProcess(
        ["mutmut"], 1, stdout="", stderr=_DEPRECATION + "DeprecationWarning: old api\n")
    assert mutmut3._tool_error_line(proc) == "DeprecationWarning: old api"


def test_mutant_glob_follows_mutmut_naming() -> None:
    assert mutmut3._mutant_glob("src/lib/doc_staleness.py") == "lib.doc_staleness.*"
    assert mutmut3._mutant_glob("pkg/calc.py") == "pkg.calc.*"
    assert mutmut3._mutant_glob("pkg/__init__.py") == "pkg.*"
    assert mutmut3._mutant_glob("src.py") == "src.*"
