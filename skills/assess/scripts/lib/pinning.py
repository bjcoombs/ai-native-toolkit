"""Does every dependency manifest have its lockfile committed? (issues #514, #786, #927)

One row per dependency manifest outside the built-in and ``.assess/config.toml``
excludes, with the lockfile found for it. Read from the files at HEAD
(``lib.command_index.repo_files``); nothing is installed or resolved.

| Manifest | Lock | Ecosystem |
|---|---|---|
| ``package.json`` (declaring dependencies) | npm, Yarn, pnpm or Bun lock, beside it or in a parent | ``node`` |
| ``pyproject.toml`` (declaring dependencies), ``Pipfile`` | ``uv.lock``, ``poetry.lock``, ``pdm.lock``, ``pylock.toml``, ``Pipfile.lock`` | ``python`` |
| ``requirements*.txt`` | itself, when every requirement is pinned with ``==`` | ``python`` |
| ``go.mod`` (with a ``require``) | ``go.sum`` | ``go`` |
| ``Cargo.toml`` | ``Cargo.lock`` | ``rust`` |
| ``Gemfile`` | ``Gemfile.lock`` | ``ruby`` |
| ``composer.json`` | ``composer.lock`` | ``php`` |
| ``mix.exs`` | ``mix.lock`` | ``elixir`` |
| ``pubspec.yaml`` | ``pubspec.lock`` | ``dart`` |
| ``Package.swift`` | ``Package.resolved`` beside it, or under ``<name>.xcworkspace/xcshareddata/swiftpm/`` (also inside an ``.xcodeproj``) in its directory | ``swift`` |
| ``*.csproj``, ``*.fsproj`` (with a ``PackageReference``) | ``packages.lock.json`` beside the project | ``dotnet`` |
| ``build.gradle``, ``build.gradle.kts`` (with a ``dependencies`` block) | ``gradle.lockfile`` beside it, ``settings-gradle.lockfile`` at the root, or ``gradle/verification-metadata.xml`` | ``jvm`` |
| ``vcpkg.json`` (with dependencies) | a ``builtin-baseline`` key in the manifest itself | ``cpp`` |
| ``conanfile.py``, ``conanfile.txt`` | ``conan.lock`` | ``cpp`` |

A lock is looked for in the manifest's directory and then each parent (a
workspace root), except the per-project .NET and Gradle locks, which sit beside
the project they lock.

Each row carries a ``note``:

- ``skipped_library``: a library whose ecosystem's convention is to commit no
  lock, with none committed: a ``composer.json`` whose ``type`` is ``library``
  or absent (Composer's default), a ``pubspec.yaml`` without ``publish_to:
  none`` (a published package) and a ``Package.swift`` declaring a
  ``.library(`` product. The row stays in ``manifests`` for the reader, but
  ``manifests_total`` and ``unlocked_total`` leave it out, so a repository of
  such libraries alone reads Layer 5 level 2 not measured (``no_manifests``)
  until a not-applicable state exists (#827). A library that commits its lock
  counts as locked as normal.
- ``direct_pins_only``: an unlocked Gradle project beside a version catalog
  (``gradle/libs.versions.toml``) or an unlocked .NET project under central
  package management (``Directory.Packages.props`` in its directory or a
  parent). Both pin direct versions, not the transitive set, so the row reads
  unlocked; the note lets the advice say to turn on dependency locking.
- null otherwise.

``Directory.Packages.props`` is read as that marker, not as a row of its own:
the locks of central package management sit beside each project.

The manifest text read to decide these (``type``, ``publish_to``, ``.library(``,
``builtin-baseline``) is never kept: a row holds paths, tokens and booleans.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from lib.assess_config import is_user_excluded
from lib.command_index import RepoIndex
from lib.doc_graph import is_excluded_path

PinNote = Literal["skipped_library", "direct_pins_only"]

_NODE_LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
               "bun.lock", "bun.lockb")
_PY_LOCKS = ("uv.lock", "poetry.lock", "pdm.lock", "pylock.toml")
# Manifest basename -> (ecosystem, lockfile names looked for beside it and in each parent).
_LOCKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "package.json": ("node", _NODE_LOCKS),
    "pyproject.toml": ("python", _PY_LOCKS),
    "Pipfile": ("python", ("Pipfile.lock",)),
    "go.mod": ("go", ("go.sum",)),
    "Cargo.toml": ("rust", ("Cargo.lock",)),
    "Gemfile": ("ruby", ("Gemfile.lock",)),
    "composer.json": ("php", ("composer.lock",)),
    "mix.exs": ("elixir", ("mix.lock",)),
    "pubspec.yaml": ("dart", ("pubspec.lock",)),
    "conanfile.py": ("cpp", ("conan.lock",)),
    "conanfile.txt": ("cpp", ("conan.lock",)),
}
_GRADLE = frozenset({"build.gradle", "build.gradle.kts"})
_DOTNET_SUFFIXES = (".csproj", ".fsproj")
_GRADLE_ROOT_LOCKS = ("settings-gradle.lockfile", "gradle/verification-metadata.xml")
_GRADLE_CATALOG = "gradle/libs.versions.toml"
_NUGET_CPM = "Directory.Packages.props"
_SWIFT_RESOLVED = re.compile(
    r"^(?:[^/]+\.xcodeproj/)?[^/]+\.xcworkspace/xcshareddata/swiftpm/Package\.resolved$")
_REQUIREMENTS = re.compile(r"^requirements[\w.-]*\.txt$")
_PUBLISH_NONE = re.compile(r"""^publish_to:\s*["']?none["']?\s*(?:#.*)?$""", re.MULTILINE)
_SWIFT_LIBRARY = re.compile(r"\.library\s*\(")
_GRADLE_DEPS = re.compile(r"^\s*dependencies\s*\{", re.MULTILINE)
_PACKAGE_REFERENCE = re.compile(r"<PackageReference\b")


class PinRow(TypedDict):
    ecosystem: str
    manifest: str
    locked: bool
    lockfile: str | None
    note: PinNote | None


class PinningBlock(TypedDict):
    manifests: list[PinRow]
    all_locked: bool | None
    unlocked_total: int
    manifests_total: int


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _json(root: Path, rel: str) -> object:
    try:
        return json.loads(_read(root, rel) or "{}")
    except json.JSONDecodeError:
        return None


def _declares_deps(root: Path, rel: str) -> bool:
    """A manifest that declares installable dependencies.

    A ``package.json`` or ``pyproject.toml`` that only holds scripts or tool
    configuration, a ``go.mod`` with no ``require``, a project with no
    ``PackageReference``, a Gradle build with no ``dependencies`` block and a
    ``vcpkg.json`` with no dependencies have nothing to lock; one that does not
    parse is skipped. Other manifests always count.
    """
    name = PurePosixPath(rel).name
    if name == "package.json":
        pkg = _json(root, rel)
        return isinstance(pkg, dict) and any(
            pkg.get(k) for k in ("dependencies", "devDependencies", "optionalDependencies",
                                 "peerDependencies"))
    if name == "vcpkg.json":
        data = _json(root, rel)
        return isinstance(data, dict) and bool(data.get("dependencies"))
    if name == "go.mod":
        # Go writes no go.sum for a module that requires nothing.
        return re.search(r"^\s*require\b", _read(root, rel), re.MULTILINE) is not None
    if name in _GRADLE:
        return _GRADLE_DEPS.search(_read(root, rel)) is not None
    if name.endswith(_DOTNET_SUFFIXES):
        return _PACKAGE_REFERENCE.search(_read(root, rel)) is not None
    if name != "pyproject.toml":
        return True
    try:
        data = tomllib.loads(_read(root, rel))
    except tomllib.TOMLDecodeError:
        return False
    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    return bool(project.get("dependencies") or project.get("optional-dependencies")
                or data.get("dependency-groups")
                or (isinstance(poetry, dict) and poetry.get("dependencies")))


def _requirement_lines(text: str) -> list[str]:
    """Direct requirements, without comments, options and ``-r`` / ``-c`` includes."""
    reqs = [ln.split("#")[0].strip() for ln in text.splitlines()]
    return [r for r in reqs if r and not r.startswith("-")]


def _requirements_pinned(text: str) -> bool:
    reqs = _requirement_lines(text)
    return bool(reqs) and all("==" in r for r in reqs)


def _join(parent: PurePosixPath, name: str) -> str:
    return str(parent / name) if str(parent) not in (".", "") else name


def _lock_near(index: RepoIndex, rel: str, locks: tuple[str, ...]) -> str | None:
    """The first lockfile in the manifest's directory or a parent (a workspace root)."""
    parent = PurePosixPath(rel).parent
    while True:
        for name in locks:
            cand = _join(parent, name)
            if cand in index.files:
                return cand
        if str(parent) in (".", ""):
            return None
        parent = parent.parent


def _beside(index: RepoIndex, rel: str, name: str) -> str | None:
    cand = _join(PurePosixPath(rel).parent, name)
    return cand if cand in index.files else None


def _swift_lock(index: RepoIndex, rel: str) -> str | None:
    """``Package.resolved`` beside the manifest, or in an Xcode workspace in its directory."""
    beside = _beside(index, rel, "Package.resolved")
    if beside:
        return beside
    parent = str(PurePosixPath(rel).parent)
    prefix = "" if parent in (".", "") else parent + "/"
    return next((f for f in sorted(index.files)
                 if f.startswith(prefix) and _SWIFT_RESOLVED.match(f[len(prefix):])), None)


def _cpm_above(index: RepoIndex, rel: str) -> bool:
    """A ``Directory.Packages.props`` in the project's directory or a parent."""
    return _lock_near(index, rel, (_NUGET_CPM,)) is not None


def _is_library(root: Path, rel: str, name: str) -> bool:
    """A manifest whose ecosystem's convention is to commit no lock (decision 2 on #927)."""
    if name == "composer.json":
        data = _json(root, rel)
        return isinstance(data, dict) and data.get("type", "library") == "library"
    if name == "pubspec.yaml":
        return _PUBLISH_NONE.search(_read(root, rel)) is None
    if name == "Package.swift":
        return _SWIFT_LIBRARY.search(_read(root, rel)) is not None
    return False


def _row(index: RepoIndex, rel: str) -> PinRow | None:
    """The row for one tracked file, or None when it is not a manifest with dependencies."""
    name = PurePosixPath(rel).name
    lock: str | None
    note: PinNote | None = None
    if _REQUIREMENTS.match(name):
        text = _read(index.root, rel)
        if not _requirement_lines(text):
            return None  # only `-r` / `-c` includes or options: nothing to pin here
        pinned = _requirements_pinned(text)
        return {"ecosystem": "python", "manifest": rel, "locked": pinned,
                "lockfile": rel if pinned else None, "note": None}
    if name in _LOCKS:
        ecosystem, locks = _LOCKS[name]
        if not _declares_deps(index.root, rel):
            return None
        lock = _lock_near(index, rel, locks)
    elif name == "Package.swift":
        ecosystem, lock = "swift", _swift_lock(index, rel)
    elif name == "vcpkg.json":
        if not _declares_deps(index.root, rel):
            return None
        data = _json(index.root, rel)
        ecosystem = "cpp"
        lock = rel if isinstance(data, dict) and data.get("builtin-baseline") else None
    elif name in _GRADLE:
        if not _declares_deps(index.root, rel):
            return None
        ecosystem = "jvm"
        lock = _beside(index, rel, "gradle.lockfile") or next(
            (f for f in _GRADLE_ROOT_LOCKS if f in index.files), None)
        if lock is None and _GRADLE_CATALOG in index.files:
            note = "direct_pins_only"
    elif name.endswith(_DOTNET_SUFFIXES):
        if not _declares_deps(index.root, rel):
            return None
        ecosystem, lock = "dotnet", _beside(index, rel, "packages.lock.json")
        if lock is None and _cpm_above(index, rel):
            note = "direct_pins_only"
    else:
        return None
    if lock is None and _is_library(index.root, rel, name):
        note = "skipped_library"
    return {"ecosystem": ecosystem, "manifest": rel, "locked": lock is not None,
            "lockfile": lock, "note": note}


def pin_rows(index: RepoIndex, excludes: tuple[set[str], list[str]] | None) -> list[PinRow]:
    """One row per dependency manifest, in path order."""
    dirs, pats = excludes if excludes else (set(), [])
    rows: list[PinRow] = []
    for rel in sorted(index.files):
        if is_excluded_path(Path(rel)) or is_user_excluded(Path(rel), dirs, pats):
            continue
        row = _row(index, rel)
        if row is not None:
            rows.append(row)
    return rows


def counted(pins: list[PinRow]) -> list[PinRow]:
    """The rows the lockfile check counts: every row but a ``skipped_library``."""
    return [p for p in pins if p["note"] != "skipped_library"]


def pinning_block(pins: list[PinRow]) -> PinningBlock:
    """``agent_environment.pinning``: the rows, and the totals over the counted ones."""
    rows = counted(pins)
    return {"manifests": pins, "all_locked": all(p["locked"] for p in rows) if rows else None,
            "unlocked_total": sum(1 for p in rows if not p["locked"]),
            "manifests_total": len(rows)}
