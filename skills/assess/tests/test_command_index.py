"""Tests for lib/command_index.py: the per-repo target index the resolver reads."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import lib.command_index as command_index
from lib.command_index import TargetSet, build_index, repo_files


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_absent_families_are_none(tmp_path: Path) -> None:
    index = build_index(_write(tmp_path, {"README.md": ""}))
    assert index.npm_scripts is None
    assert index.make_targets is None
    assert index.just_recipes is None
    assert index.tox_envs is None
    assert index.nox_sessions is None
    assert not index.has_pyproject and not index.pytest_present
    assert index.ci_lines == ()


def test_target_set_lenient() -> None:
    assert TargetSet(frozenset({"a"})).has("a")
    assert not TargetSet(frozenset({"a"})).has("b")
    assert TargetSet(frozenset(), lenient=True).has("anything")


def test_unions_configs_but_skips_excluded_trees(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        "package.json": json.dumps({"scripts": {"test": "x"}}),
        "apps/web/package.json": json.dumps({"scripts": {"dev": "vite"}, "dependencies": {"react": "18"}}),
        "node_modules/dep/package.json": json.dumps({"scripts": {"postinstall": "x"}}),
        "tests/fixtures/sample/Makefile": "fixture-only:\n\ttrue\n",
        "tests/fixtures/sample/go.mod": "module x\n",
        "build/Makefile": "built:\n\ttrue\n",
        "mk/rules.mk": "deploy:\n\ttrue\n",
    })
    index = build_index(root)
    assert index.npm_scripts == TargetSet(frozenset({"test", "dev"}))
    assert "react" in index.npm_packages
    assert index.make_targets is not None and index.make_targets.names == frozenset({"deploy"})
    assert not index.has_basename("go.mod")  # only under tests/fixtures
    assert "node_modules/dep/package.json" not in index.files  # the walk prunes excluded dirs


def test_makefile_parsing(tmp_path: Path) -> None:
    root = _write(tmp_path, {"Makefile": (
        "VAR := 1\n"
        "OTHER = 2\n"
        "all build: deps\n"
        "\tcompile: not a target\n"
        "  indented: neither\n"
        "test::\n"
        ".PHONY: all\n"
        "# comment: no\n"
    )})
    targets = build_index(root).make_targets
    assert targets is not None
    assert targets.names == frozenset({"all", "build", "test"})
    assert not targets.lenient


def test_justfile_parsing(tmp_path: Path) -> None:
    root = _write(tmp_path, {"justfile": (
        "set dotenv-load\n"
        "export FOO := \"1\"\n"
        "alias b := build\n"
        "build target='x':\n"
        "    echo\n"
        "@quiet:\n"
        "    echo\n"
        "VERSION := \"1\"\n"
    )})
    recipes = build_index(root).just_recipes
    assert recipes is not None
    assert recipes.names == frozenset({"b", "build", "quiet"})


def test_python_names_and_pytest(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        "pyproject.toml": (
            "[project]\nname='x'\ndependencies=['Requests>=2', 7]\n"
            "[project.optional-dependencies]\ndocs=['sphinx']\n"
            "[project.scripts]\nmy-cli='x:main'\n"
            "[tool.mypy]\nstrict=true\n"
        ),
        "requirements-dev.in": "black\n",
    })
    index = build_index(root)
    assert {"requests", "sphinx", "my-cli", "mypy", "black"} <= index.python_names
    assert not index.pytest_present
    assert build_index(_write(tmp_path / "b", {"tox.ini": "[pytest]\n"})).pytest_present
    assert build_index(_write(tmp_path / "c", {"pkg/conftest.py": ""})).pytest_present


def test_tox_and_nox_parsing(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        "tox.ini": "[tox]\nenvlist =\n    py311\n    lint\n[testenv:docs]\n",
        "pyproject.toml": "[tool.tox]\nenv_list = [\"type\"]\n[tool.tox.env.fmt]\n",
        "noxfile.py": (
            "import nox\n@nox.session(python=['3.11'], name='unit-tests')\n"
            "@nox.parametrize('x', [1])\ndef unit(session):\n    pass\n"
        ),
    })
    index = build_index(root)
    assert index.tox_envs is not None
    assert {"py311", "lint", "docs", "type", "fmt"} <= index.tox_envs.names
    assert index.nox_sessions is not None
    assert index.nox_sessions.names == frozenset({"unit", "unit-tests"})


def test_ci_lines_are_normalised(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        ".github/workflows/ci.yaml": "steps:\n  - run:   npm   ci\n  - name: x\n    run: |\n      make  lint\n",
        ".github/workflows/notes.txt": "run: ignored\n",
    })
    lines = build_index(root).ci_lines
    assert "npm ci" in lines and "make lint" in lines
    assert "ignored" not in lines


def test_oversized_config_is_skipped(tmp_path: Path, monkeypatch) -> None:
    root = _write(tmp_path, {"package.json": json.dumps({"scripts": {"big": "x"}})})
    monkeypatch.setattr(command_index, "MAX_CONFIG_BYTES", 5)
    index = build_index(root)
    assert index.npm_scripts == TargetSet(frozenset())


def test_paths_dirs_and_basenames(tmp_path: Path) -> None:
    index = build_index(_write(tmp_path, {"a/b/c.txt": "", "top.md": ""}))
    assert index.has_path("a/b/c.txt") and index.has_path("a/b/") and index.has_path("a")
    assert not index.has_path("a/x")
    assert index.has_file("top.md") and not index.has_file("a")
    assert index.has_basename("c.txt")


def test_repo_files_in_git_lists_tracked_only(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, {"tracked.txt": ""})
    (repo / "src").mkdir()
    (repo / "alias").symlink_to("src")
    commit("init")
    _write(repo, {"untracked.txt": ""})
    files = repo_files(repo)
    assert files == frozenset({"tracked.txt", "alias"})
    sub = repo / "nested"
    _write(sub, {"inner.txt": ""})
    commit("nested")
    assert repo_files(sub) == frozenset({"inner.txt"})  # relative to the given root


def test_non_utf8_tracked_filename_does_not_abort(git_repo) -> None:  # type: ignore[no-untyped-def]  # conftest fixture
    """A tracked name that is not UTF-8 (added straight to the index, since
    some filesystems refuse to create one) degrades, never raises."""
    repo, commit = git_repo
    _write(repo, {"Makefile": "check:\n\ttrue\n"})
    commit("init")
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "Makefile"],
                          capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run([b"git", b"-C", str(repo).encode(), b"update-index", b"--add", b"--cacheinfo",
                    b"100644," + blob.encode() + b",caf\xe9.txt"], check=True)
    files = repo_files(repo)
    assert "Makefile" in files
    assert len(files) == 2
    assert build_index(repo).make_targets is not None
