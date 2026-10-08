"""A committed directory symlink (``skills/assess/src -> scripts``, which the
``[tool.mutmut]`` config mutates through) must not make any walk count the
linked files twice: the treemap keys files by resolved path, and the shared
walks do not descend the link."""
from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from lib.test_pressure.common import _iter_files
from test_complexity_treemap import _load_treemap

REPO = Path(__file__).resolve().parents[3]


def _linked_repo(root: Path) -> Path:
    pkg = root / "pkg"
    (pkg / "scripts" / "lib").mkdir(parents=True)
    (pkg / "scripts" / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "scripts" / "lib" / "a.py").write_text("def f():\n    return 1\n",
                                                  encoding="utf-8")
    (pkg / "src").symlink_to("scripts")
    return pkg / "scripts" / "lib" / "a.py"


def test_treemap_counts_a_file_reached_through_a_link_once(
        tmp_path: Path, monkeypatch) -> None:
    """Even a walker that follows the link (both spellings reported) leaves
    one treemap entry, because lizard and scc results are keyed by resolved
    path."""
    real = _linked_repo(tmp_path)
    treemap = _load_treemap()
    spellings = [real, tmp_path / "pkg" / "src" / "lib" / "a.py"]
    fake = [types.SimpleNamespace(filename=str(p), nloc=2, function_list=[
        types.SimpleNamespace(cyclomatic_complexity=1, name="f")]) for p in spellings]
    monkeypatch.setattr(treemap.lizard, "analyze", lambda **_k: iter(fake), raising=False)
    monkeypatch.setattr(treemap.shutil, "which", lambda _t: None)
    scores = treemap.lizard_scores(tmp_path.resolve())
    assert list(scores) == [real.resolve()]


def test_shared_walks_do_not_descend_a_directory_link(tmp_path: Path) -> None:
    """``_iter_files`` (test_pressure, sibling of the doc-graph walk) and the
    ``rglob`` that structure_graph uses each see the linked files once."""
    real = _linked_repo(tmp_path)
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in _iter_files(tmp_path))
    assert rel == ["pkg/scripts/lib/__init__.py", "pkg/scripts/lib/a.py"]
    inits = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("__init__.py")]
    assert inits == ["pkg/scripts/lib/__init__.py"]
    assert real.is_file()


def test_repo_src_link_points_at_scripts() -> None:
    link = REPO / "skills" / "assess" / "src"
    if not link.is_symlink():
        pytest.skip("not a checkout with the committed src link")
    assert link.readlink() == Path("scripts")


def test_scratch_copy_keeps_a_tracked_directory_link(tmp_path: Path) -> None:
    """The mutation pass copies the tree from ``git ls-files``, which lists a
    link as one entry; the copy recreates the link rather than dropping it."""
    import subprocess

    from lib.test_pressure import mutmut3

    repo = tmp_path / "repo"
    repo.mkdir()
    _linked_repo(repo)
    for cmd in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    dest = tmp_path / "copy"
    dest.mkdir()
    assert mutmut3._copy_repo(repo, dest) is True
    link = dest / "pkg" / "src"
    assert link.is_symlink() and link.readlink() == Path("scripts")
    assert (link / "lib" / "a.py").is_file()
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    (repo / ".git").rename(tmp_path / "git-aside")
    assert mutmut3._copy_repo(repo, fallback) is False
    # outside git, links are followed: the target's files, never a link
    assert not (fallback / "pkg" / "src").is_symlink()
    assert (fallback / "pkg" / "src" / "lib" / "a.py").is_file()


def test_scratch_copy_never_links_back_into_the_assessed_tree(tmp_path: Path) -> None:
    """A link out of the repository (absolute, or climbing past its root)
    would let a suite write through the copy into real files: a file target is
    copied as content, a directory target left out."""
    import subprocess

    from lib.test_pressure import mutmut3

    outside = tmp_path / "outside"
    (outside / "data").mkdir(parents=True)
    (outside / "note.txt").write_text("n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    real = _linked_repo(repo)
    (repo / "abs_dir").symlink_to(outside / "data")
    (repo / "abs_file").symlink_to(outside / "note.txt")
    (repo / "pkg" / "up_file").symlink_to("../../outside/note.txt")
    (repo / "pkg" / "in_file").symlink_to(real)  # absolute but inside the repo
    for cmd in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    dest = tmp_path / "copy"
    dest.mkdir()
    assert mutmut3._copy_repo(repo, dest) is True
    assert not (dest / "abs_dir").exists() and not (dest / "abs_dir").is_symlink()
    for rel in ("abs_file", "pkg/up_file"):
        assert not (dest / rel).is_symlink() and (dest / rel).read_text() == "n"
    in_file = dest / "pkg" / "in_file"
    assert in_file.is_symlink() and not Path(os.readlink(in_file)).is_absolute()
    assert in_file.resolve() == (dest / "pkg" / "scripts" / "lib" / "a.py").resolve()


def test_own_config_through_a_link_maps_focus_paths_both_ways(
        tmp_path: Path, monkeypatch) -> None:
    """The ``[tool.mutmut]`` shape of this repo: ``source_paths = ["src"]`` with
    ``src -> scripts``. A focus file named ``pkg/scripts/lib/a.py`` is in scope
    as ``src/lib/a.py``, and its result comes back under the focus name."""
    import json
    import subprocess

    import lib.test_pressure as tp
    from lib.test_pressure import mutation, run_bounded_mutation

    _linked_repo(tmp_path)
    (tmp_path / "pkg" / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["src"]\nonly_mutate = ["src/lib/a.py"]\n',
        encoding="utf-8")
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)
    monkeypatch.setattr(mutation, "_mutmut_major", lambda _exe: 3)
    seen: list[bool] = []

    def run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        cwd = Path(kwargs["cwd"])
        seen.append((cwd / "src" / "lib" / "a.py").is_file())
        meta = cwd / "mutants" / "src" / "lib" / "a.py.meta"
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps({"exit_code_by_key": {"k": 1, "s": 0}}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tp.subprocess, "run", run)
    r = run_bounded_mutation(tmp_path, hot_files=["pkg/scripts/lib/a.py"], opt_in=True)
    assert seen == [True]
    assert r["groups"][0]["config"] == "repo"
    assert r["per_file"] == [
        {"file": "pkg/scripts/lib/a.py", "killed": 1, "survived": 1, "total": 2}]
