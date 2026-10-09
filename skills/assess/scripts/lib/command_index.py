"""Index the build, test and task targets a repository defines, for command resolution.

``lib.command_resolver`` asks one question of a repository: does the target a
command names exist? This module answers it from the files at HEAD, once per
repository, without running anything:

- ``package.json`` scripts and declared dependencies (every tracked
  ``package.json`` outside the excluded trees, unioned, so a workspace
  package's script resolves);
- Makefile targets (``Makefile``, ``makefile``, ``GNUmakefile``, ``*.mk``) and
  justfile recipes, with a lenient flag when a target name is computed
  (``$(VAR)``) or a pattern rule (``%``) could match anything;
- ``pyproject.toml``: declared dependencies, ``[project.scripts]`` and the
  ``[tool.*]`` tables (``[tool.ruff]`` means ``ruff`` is the repo's tool);
  whether pytest is configured; tox environments and nox sessions;
- the lines of the CI configuration (GitHub Actions workflows, GitLab CI), so a
  command a CI step runs verbatim resolves even when its runner is unknown.

Files come from ``git ls-files`` when the root is a git repository (tracked
files only - an untracked Makefile is not part of what the repo ships; a
tracked symlink is listed under its own path), else from a walk that skips
``lib.doc_graph.EXCLUDE_DIRS``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from lib.doc_graph import is_excluded_path

MAKEFILE_NAMES = frozenset({"Makefile", "makefile", "GNUmakefile"})
JUSTFILE_NAMES = frozenset({"justfile", "Justfile", ".justfile", "JUSTFILE"})
CI_CONFIG_PREFIXES = (".github/workflows/",)
CI_CONFIG_FILES = frozenset({".gitlab-ci.yml"})
PYTEST_CONFIG_FILES = frozenset({"pytest.ini", "conftest.py"})

# Largest config file read, in bytes. A generated multi-megabyte package.json
# lock-in or vendored Makefile is not where an instruction file's targets live.
MAX_CONFIG_BYTES = 2_000_000

_MAKE_RULE = re.compile(r"^([^\s:#=][^:#=]*?)\s*::?(?!=)")
_JUST_RECIPE = re.compile(r"^@?([A-Za-z_][A-Za-z0-9_-]*)(?:\s+[^:]*?)?\s*:(?!=)")
_JUST_ALIAS = re.compile(r"^alias\s+([A-Za-z_][\w-]*)\s*:=")
_TOX_SECTION = re.compile(r"^\[(?:testenv|tool\.tox\.env|env):?\.?([\w.-]+)\]", re.MULTILINE)
_TOX_ENVLIST = re.compile(r"^\s*env_?list\s*=\s*(.+?)(?=^\S|\Z)", re.MULTILINE | re.DOTALL)
_NOX_SESSION = re.compile(
    r"@nox\.session(?:\((?P<args>[^)]*)\))?\s*\n(?:\s*@[^\n]*\n)*\s*def\s+(?P<name>\w+)"
)
_NOX_NAME = re.compile(r"name\s*=\s*[\"']([^\"']+)[\"']")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class TargetSet:
    """Names a config file family defines; ``lenient`` when any name could match."""

    names: frozenset[str]
    lenient: bool = False

    def has(self, name: str) -> bool:
        return self.lenient or name in self.names


@dataclass(frozen=True)
class RepoIndex:
    """What one repository defines, for ``lib.command_resolver.resolve``.

    A ``TargetSet`` field is ``None`` when the repo has no file of that family
    at all (no Makefile), which is a different answer from a Makefile that
    lacks the target.
    """

    root: Path
    files: frozenset[str]
    dirs: frozenset[str]
    basenames: frozenset[str]
    npm_scripts: TargetSet | None
    npm_packages: frozenset[str]
    make_targets: TargetSet | None
    just_recipes: TargetSet | None
    has_pyproject: bool
    has_poetry: bool
    python_names: frozenset[str]
    pytest_present: bool
    tox_envs: TargetSet | None
    nox_sessions: TargetSet | None
    ci_lines: tuple[str, ...]

    def has_file(self, rel: str) -> bool:
        return rel in self.files

    def has_path(self, rel: str) -> bool:
        rel = rel.rstrip("/")
        return rel in self.files or rel in self.dirs

    def has_basename(self, name: str) -> bool:
        """True when a file of this name exists outside the excluded trees."""
        return name in self.basenames


GIT_TIMEOUT_SECONDS = 30


def _git_ls_files(root: Path) -> frozenset[str] | None:
    """Tracked paths relative to ``root``, symlinks as entries (not resolved); None if not git."""
    try:
        raw = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return frozenset(rel for rel in raw.split("\0") if rel)


def repo_files(repo_root: Path) -> frozenset[str]:
    """Repo-relative POSIX paths of the files at HEAD (tracked), or a walk when not git."""
    root = repo_root.resolve()
    tracked = _git_ls_files(root)
    if tracked is not None:
        return tracked
    walked: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if not is_excluded_path(rel_dir / d)]
        walked.update((rel_dir / name).as_posix() for name in filenames)
    return frozenset(walked)


def _parent_dirs(files: Iterable[str]) -> frozenset[str]:
    dirs: set[str] = set()
    for rel in files:
        parent = PurePosixPath(rel).parent
        while str(parent) not in (".", ""):
            dirs.add(str(parent))
            parent = parent.parent
    return frozenset(dirs)


def _read(root: Path, rel: str) -> str:
    path = root / rel
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _config_files(files: frozenset[str], pred: Any) -> list[str]:
    return sorted(
        f for f in files
        if pred(PurePosixPath(f)) and not is_excluded_path(Path(f))
    )


def _npm(root: Path, files: frozenset[str]) -> tuple[TargetSet | None, frozenset[str]]:
    manifests = _config_files(files, lambda p: p.name == "package.json")
    if not manifests:
        return None, frozenset()
    scripts: set[str] = set()
    packages: set[str] = set()
    for rel in manifests:
        try:
            data = json.loads(_read(root, rel) or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("scripts"), dict):
            scripts.update(str(k) for k in data["scripts"])
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            if isinstance(data.get(key), dict):
                packages.update(str(k) for k in data[key])
    return TargetSet(frozenset(scripts)), frozenset(packages)


def _make_targets(text: str) -> tuple[set[str], bool]:
    names: set[str] = set()
    lenient = False
    for line in text.splitlines():
        if line.startswith(("\t", " ")):
            continue
        match = _MAKE_RULE.match(line)
        if not match:
            continue
        for name in match.group(1).split():
            if "$" in name or "%" in name:
                lenient = True
            elif not name.startswith("."):
                names.add(name)
    return names, lenient


def _make(root: Path, files: frozenset[str]) -> TargetSet | None:
    makefiles = _config_files(files, lambda p: p.name in MAKEFILE_NAMES or p.suffix == ".mk")
    if not makefiles:
        return None
    names: set[str] = set()
    lenient = False
    for rel in makefiles:
        found, loose = _make_targets(_read(root, rel))
        names |= found
        lenient = lenient or loose
    return TargetSet(frozenset(names), lenient)


def _just(root: Path, files: frozenset[str]) -> TargetSet | None:
    justfiles = _config_files(files, lambda p: p.name in JUSTFILE_NAMES or p.suffix == ".just")
    if not justfiles:
        return None
    names: set[str] = set()
    lenient = False
    for rel in justfiles:
        for line in _read(root, rel).splitlines():
            if line.startswith("mod ") or line.startswith("import "):
                lenient = True  # recipes live in another file the module names
            alias = _JUST_ALIAS.match(line)
            recipe = _JUST_RECIPE.match(line)
            if alias:
                names.add(alias.group(1))
            elif recipe and not line.startswith(("set ", "export ")):
                names.add(recipe.group(1))
    return TargetSet(frozenset(names), lenient)


def _requirement_names(specs: Iterable[Any]) -> set[str]:
    names: set[str] = set()
    for spec in specs:
        if isinstance(spec, str):
            match = _REQUIREMENT_NAME.match(spec)
            if match:
                names.add(match.group(1).lower())
    return names


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pyproject_names(data: dict[str, Any]) -> set[str]:
    """Tool and dependency names a pyproject declares."""
    project = _table(data, "project")
    names = _requirement_names(_list(project.get("dependencies")))
    for group in _table(project, "optional-dependencies").values():
        names |= _requirement_names(_list(group))
    for group in _table(data, "dependency-groups").values():
        names |= _requirement_names(_list(group))
    names.update(str(k).lower() for k in _table(project, "scripts"))
    tool = _table(data, "tool")
    names.update(str(k).lower() for k in tool)
    poetry = _table(tool, "poetry")
    for key in ("dependencies", "dev-dependencies", "scripts"):
        names.update(str(k).lower() for k in _table(poetry, key))
    for group in _table(poetry, "group").values():
        if isinstance(group, dict):
            names.update(str(k).lower() for k in _table(group, "dependencies"))
    return names


def _python(root: Path, files: frozenset[str]) -> tuple[bool, bool, frozenset[str], bool]:
    """(has_pyproject, has_poetry, declared names, pytest configured)."""
    pyprojects = _config_files(files, lambda p: p.name == "pyproject.toml")
    names: set[str] = set()
    poetry = False
    for rel in pyprojects:
        try:
            data = tomllib.loads(_read(root, rel))
        except tomllib.TOMLDecodeError:
            continue
        names |= _pyproject_names(data)
        poetry = poetry or "poetry" in _table(data, "tool")
    requirements = _config_files(
        files, lambda p: p.name.startswith("requirements") and p.suffix in (".txt", ".in")
    )
    for rel in requirements:
        names |= _requirement_names(_read(root, rel).splitlines())
    pytest_files = _config_files(
        files,
        lambda p: p.name in PYTEST_CONFIG_FILES
        or (p.name.startswith("test_") and p.suffix == ".py"),
    )
    pytest_present = "pytest" in names or bool(pytest_files) or any(
        "[tool:pytest]" in _read(root, rel) or "[pytest]" in _read(root, rel)
        for rel in _config_files(files, lambda p: p.name in ("setup.cfg", "tox.ini"))
    )
    return bool(pyprojects), poetry, frozenset(names), pytest_present


def _tox(root: Path, files: frozenset[str]) -> TargetSet | None:
    texts = [_read(root, rel) for rel in _config_files(files, lambda p: p.name in ("tox.ini", "tox.toml"))]
    texts += [
        text for rel in _config_files(files, lambda p: p.name in ("pyproject.toml", "setup.cfg"))
        if "[tool.tox" in (text := _read(root, rel)) or "[tox:tox]" in text
    ]
    if not texts:
        return None
    names: set[str] = set()
    lenient = False
    for text in texts:
        names.update(m.group(1) for m in _TOX_SECTION.finditer(text))
        for match in _TOX_ENVLIST.finditer(text):
            value = match.group(1)
            lenient = lenient or "{" in value
            names.update(t for t in re.split(r"[\s,\[\]\"']+", value) if t)
    return TargetSet(frozenset(names), lenient)


def _nox(root: Path, files: frozenset[str]) -> TargetSet | None:
    noxfiles = _config_files(files, lambda p: p.name == "noxfile.py")
    if not noxfiles:
        return None
    names: set[str] = set()
    for rel in noxfiles:
        for match in _NOX_SESSION.finditer(_read(root, rel)):
            names.add(match.group("name"))
            named = _NOX_NAME.search(match.group("args") or "")
            if named:
                names.add(named.group(1))
    return TargetSet(frozenset(names))


def _ci_lines(root: Path, files: frozenset[str]) -> tuple[str, ...]:
    ci_files = sorted(
        f for f in files
        if f in CI_CONFIG_FILES
        or (f.startswith(CI_CONFIG_PREFIXES) and f.endswith((".yml", ".yaml")))
    )
    lines: list[str] = []
    for rel in ci_files:
        for line in _read(root, rel).splitlines():
            text = re.sub(r"^\s*(?:-\s+)?(?:run:|script:)?\s*", "", line)
            text = " ".join(text.split())
            if text:
                lines.append(text)
    return tuple(lines)


def build_index(repo_root: Path, files: frozenset[str] | None = None) -> RepoIndex:
    """Read every config family once and return the repository's ``RepoIndex``."""
    root = repo_root.resolve()
    files = repo_files(root) if files is None else files
    npm_scripts, npm_packages = _npm(root, files)
    has_pyproject, has_poetry, python_names, pytest_present = _python(root, files)
    return RepoIndex(
        root=root,
        files=files,
        dirs=_parent_dirs(files),
        basenames=frozenset(
            PurePosixPath(f).name for f in files if not is_excluded_path(Path(f))
        ),
        npm_scripts=npm_scripts,
        npm_packages=npm_packages,
        make_targets=_make(root, files),
        just_recipes=_just(root, files),
        has_pyproject=has_pyproject,
        has_poetry=has_poetry,
        python_names=python_names,
        pytest_present=pytest_present,
        tox_envs=_tox(root, files),
        nox_sessions=_nox(root, files),
        ci_lines=_ci_lines(root, files),
    )
