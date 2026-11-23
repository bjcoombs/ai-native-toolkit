"""Monorepo unit discovery (issue #862, step 1 of #800).

A unit is an independently built area of a repository. The run context's
``units`` block lists them; later steps evaluate each unit and roll them up.

**Discovered units.** From the tracked files (``command_index.repo_files``),
minus the doc graph's ``EXCLUDE_DIRS`` and the ``.assess/config.toml`` excludes:

1. A folder holding a build manifest (``MANIFESTS``) is a candidate. Candidates
   are taken shallowest first, so a manifest inside a unit is part of that unit.
2. A candidate that declares a workspace is an **aggregator**, not a unit. Each
   declared member is a unit instead, and the search goes on below the
   aggregator outside its members (``openai/codex``: the root pnpm workspace
   lists ``codex-cli``, and ``codex-rs/`` is a Cargo workspace of its own).
   Every declaration in the folder is read and their members unioned (a Cargo
   workspace with npm bindings beside it), each member tagged with its ecosystem.
   A member that declares a workspace of its own is an aggregator in turn, except
   that Maven ``<modules>`` are expanded one level only: a ``pom`` member with
   modules is one unit, as the Helidon spike on #800 counted its top-level areas.
   The aggregator itself is a unit of the source files no member or nested
   manifest owns when it is a member too (``go.work`` ``use .``, a Cargo
   ``[package]``, a uv ``[project]``) or holds such files (an npm or pnpm root
   with ``src/``). A workspace that lists no member is a plain unit. Workspace
   declarations read:

   - Cargo: ``[workspace] members`` and ``exclude`` in ``Cargo.toml``;
   - pnpm: ``packages`` in ``pnpm-workspace.yaml`` (``!`` excludes; a file without
     ``packages`` holds settings only and is no workspace);
   - npm and Yarn: ``workspaces`` in ``package.json``, a list or ``{packages}``;
   - Go: ``use`` in ``go.work``;
   - uv: ``[tool.uv.workspace] members`` and ``exclude`` in ``pyproject.toml``;
   - Gradle: ``include`` in ``settings.gradle`` or ``settings.gradle.kts``, over
     several lines too (``includeBuild`` is no member);
   - Gradle ``project(':x').projectDir = file('dir')`` moves a member to ``dir``;
   - Maven: ``<module>`` entries in ``pom.xml``.

   A member pattern may be a glob (``crates/*``, ``packages/**``); a glob match
   counts only when the folder holds a manifest, and one that matches no folder is
   skipped as ``no_match``. A literal member that no
   tracked file sits under is skipped as ``missing``, and one that resolves
   outside the repository as ``outside_repo``; a member inside a unit found earlier
   is skipped as ``claimed``. A manifest folder above units already claimed (a
   ``packages/package.json`` over the ``packages/*`` members) is a unit of the
   source files no nested manifest owns, so units never nest.
3. A root ``pyproject.toml`` or ``package.json`` that declares no package (only
   ``[tool.ruff]``; a name and devDependencies with no entry point or runtime
   dependency) claims nothing: its folder is a unit of
   the source files no nested manifest owns, or ``tooling_only`` without any, so
   a tooling root does not fold a polyglot repository into one unit.
4. A unit with no source file below it (``doc_graph.CODE_EXTENSIONS``) is a
   manifest-only aggregator (a BOM, a parent POM) and is skipped as
   ``manifest_only``.

**Unparseable manifests (#1030).** A manifest that cannot be parsed nulls the count
only when it could change the unit set: it sits at the repository root, at a
workspace member, or beside a declaration that parsed as a workspace, or it is named
as a workspace manifest (``WORKSPACE_ONLY``: ``pnpm-workspace.yaml``, ``go.work``,
``settings.gradle``, ``settings.gradle.kts``). Such a file is named in
``unreadable_manifests``; its folder is an aggregator with ``member_count`` null,
and ``unit_count`` reads null with ``unit_count_reason`` ``unreadable_manifest``:
its members are unknown. Any other unparseable manifest is a leaf, such as a broken
test fixture outside the member globs: it is skipped as ``unparseable_leaf`` (the
manifest's path) and its folder is still a candidate, as if the broken file
declared no workspace. The count stays known. A workspace member shallower than its
workspace (``deep/ws`` listing ``../../lib``) is visited first as a plain candidate;
when the workspace later lists it, its leaf skip moves to ``unreadable_manifests``.
A repository with no build manifest has no units (``unit_count`` 0).

**Skill and plugin-component units (#1098).** Before the manifest walk,
``lib.unit_components`` finds each folder holding a ``SKILL.md`` (kind ``skill``) and each
plugin root's ``agents/`` and ``commands/`` (kinds ``agents``, ``commands``), shallowest
first. They are units of origin ``component``, claimed before any manifest: a manifest
inside one is part of it (a skill with a ``pyproject.toml`` is one ``skill`` unit with the
manifest's ecosystem) and is listed in ``skipped`` as ``claimed`` (unless it is test data), and a
manifest folder above them is a unit of the source files no
component or nested manifest owns. Every other unit is of kind ``package``.

**Declared units.** ``[[units]]`` in ``.assess/config.toml`` replaces discovery::

    [[units]]
    path = "services/billing"   # repo-relative folder, required; "." is the root
    name = "Billing"            # optional display name

An entry is skipped, with a ``PROBLEMS`` code and a warning on stderr, when it
is not a table, has no usable path, names a folder with no tracked file, or
repeats a path. A declared folder holding a ``SKILL.md`` is of kind ``skill``, any other
``package``. Declared units are used when at least one entry is valid;
otherwise discovery runs and the skipped entries are still listed.

Counts-only output drops every path and name (``checks/core/units.toml``): it
keeps the counts, the kind, origin and ecosystem tokens and the skip codes. A unit's folder
alone survives as its salted hash under ``--hash-paths`` (class ``hashed_path``, #866),
the join key of a fleet unit row. Pure apart
from reading the file list and the manifests.
"""
from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NamedTuple, TypedDict

from lib.assess_config import _clean_rel, is_user_excluded, load_config
from lib.command_index import repo_files
from lib.doc_graph import CODE_EXTENSIONS, EXCLUDE_DIRS
from lib.path_classes import is_production
from lib.unit_components import SKILL_FILE, Kind, discover_components

Ecosystem = Literal["cargo", "pnpm", "npm", "go", "uv", "gradle", "maven", "python"]
Origin = Literal["manifest", "workspace_member", "config", "component"]

# Build manifests that make a folder a unit candidate, with the ecosystem a plain
# (non-workspace) unit is labelled by: the first present in this order.
MANIFESTS: tuple[tuple[str, Ecosystem], ...] = (
    ("Cargo.toml", "cargo"),
    ("go.work", "go"),
    ("go.mod", "go"),
    ("pnpm-workspace.yaml", "pnpm"),
    ("package.json", "npm"),
    ("settings.gradle.kts", "gradle"),
    ("settings.gradle", "gradle"),
    ("build.gradle.kts", "gradle"),
    ("build.gradle", "gradle"),
    ("pom.xml", "maven"),
    ("pyproject.toml", "python"),
)
MANIFEST_NAMES = frozenset(name for name, _ in MANIFESTS)

SECTION = "units"
UNREADABLE_REASON = "unreadable_manifest"
# Files that exist only to declare a workspace: unparseable, they always null the count.
WORKSPACE_ONLY = frozenset({"pnpm-workspace.yaml", "go.work", "settings.gradle", "settings.gradle.kts"})
SOURCES = ("discovered", "config")
# Why a unit candidate or a [[units]] entry was skipped, as SkippedUnit.problem holds it.
PROBLEMS = (
    "manifest_only", "missing", "no_match", "outside_repo", "claimed", "tooling_only", "unparseable_leaf",
    "not_a_list", "not_a_table", "no_path", "duplicate",
)
_GLOB_CHARS = frozenset("*?[")


class UnitEntry(TypedDict):
    """One unit: its folder, kind, how it was found, and its workspace when it is a member."""

    path: str
    kind: Kind  # package (a build manifest), skill, agents or commands (lib.unit_components)
    origin: Origin
    ecosystem: Ecosystem | None  # None for a declared unit
    workspace: str | None  # the aggregator folder of a workspace member
    name: str | None  # a declared unit's display name


class AggregatorEntry(TypedDict):
    """A workspace root: not a unit itself; ``member_count`` units were declared by it."""

    path: str
    ecosystem: Ecosystem
    member_count: int | None  # None when its manifest cannot be parsed


class SkippedUnit(TypedDict):
    """A candidate or declared unit left out, with a ``PROBLEMS`` code."""

    path: str | None
    problem: str


class UnitsBlock(TypedDict):
    """The run context's ``units`` block (#862)."""

    source: Literal["discovered", "config"]
    unit_count: int | None  # None, with unit_count_reason, when a manifest that could change the units is unreadable
    unit_count_reason: str | None
    units: list[UnitEntry]
    aggregators: list[AggregatorEntry]
    aggregator_count: int
    skipped: list[SkippedUnit]
    unreadable_manifests: list[str]


class _Workspace(NamedTuple):
    ecosystem: Ecosystem
    members: list[str]  # patterns relative to the aggregator folder
    excludes: list[str]
    root_member: bool = False  # the root manifest is a package too (Cargo [package], uv [project])


class _Unreadable(Exception):
    """A build manifest that exists but cannot be parsed."""


# -- workspace declarations ---------------------------------------------------

def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _toml(path: Path) -> Mapping[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e


def _cargo(path: Path) -> _Workspace | None:
    data = _toml(path)
    ws = data.get("workspace")
    if not isinstance(ws, Mapping):
        return None
    return _Workspace("cargo", _strings(ws.get("members")), _strings(ws.get("exclude")),
                      root_member="package" in data)


def _uv(path: Path) -> _Workspace | None:
    data = _toml(path)
    tool = data.get("tool")
    uv = tool.get("uv") if isinstance(tool, Mapping) else None
    ws = uv.get("workspace") if isinstance(uv, Mapping) else None
    if not isinstance(ws, Mapping):
        return None
    # a uv root with a [project] is a member too; a virtual workspace (no [project]) is not
    return _Workspace("uv", _strings(ws.get("members")), _strings(ws.get("exclude")),
                      root_member="project" in data)


def _split_negated(patterns: Iterable[str]) -> tuple[list[str], list[str]]:
    members = [p for p in patterns if not p.startswith("!")]
    return members, [p[1:] for p in patterns if p.startswith("!")]


def _npm(path: Path) -> _Workspace | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    if not isinstance(data, Mapping) or "workspaces" not in data:
        return None
    ws = data["workspaces"]
    patterns = _strings(ws.get("packages") if isinstance(ws, Mapping) else ws)
    return _Workspace("npm", *_split_negated(patterns))


_YAML_ITEM = re.compile(r"""^\s*-\s*['"]?([^'"#]+?)['"]?\s*(?:#.*)?$""")
_YAML_FLOW = re.compile(r"""^\[(.*)\]$""")


def _pnpm(path: Path) -> _Workspace | None:
    """``packages:`` list items, read line by line (no YAML dependency in the core).

    None when the file has no ``packages:`` key: pnpm 10 keeps settings such as
    ``onlyBuiltDependencies`` there in a single-package repository. A ``packages:``
    value this reader cannot parse raises ``_Unreadable``.
    """
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    patterns: list[str] = []
    found = inside = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace() and not line.startswith("-"):
            key, _, value = line.split("#")[0].partition(":")
            inside = key.strip() == "packages"
            found = found or inside
            value = value.strip()
            if inside and value:
                flow = _YAML_FLOW.match(value)
                if flow is None:
                    raise _Unreadable(str(path))
                patterns += [item.strip().strip("'\"") for item in flow.group(1).split(",")
                             if item.strip()]
                inside = False
            continue
        match = _YAML_ITEM.match(line) if inside else None
        if match:
            patterns.append(match.group(1).strip())
    return _Workspace("pnpm", *_split_negated(patterns)) if found else None


_GO_USE = re.compile(r"^\s*use\s+(?:\(\s*(?P<block>[^)]*)\)|(?P<one>\S+))", re.MULTILINE)


def _go(path: Path) -> _Workspace:
    try:
        text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    members: list[str] = []
    for match in _GO_USE.finditer(text):
        members.extend((match.group("block") or match.group("one") or "").split())
    return _Workspace("go", [m.strip('"') for m in members], [])


# ``include(":a",\n ":b")`` (parentheses, any lines) or ``include ':a', ':b'`` (Groovy,
# continued on the next line after a trailing comma).
_GRADLE_INCLUDE = re.compile(r"\binclude\b(?!Build)\s*(?:\(([^)]*)\)|((?:[^\n]*,[ \t]*\n)*[^\n]*))")
_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")
# ``project(':core').projectDir = file('modules/core')``: the member lives elsewhere.
_GRADLE_PROJECT_DIR = re.compile(
    r"""project\(\s*['"]([^'"]+)['"]\s*\)\.projectDir\s*=\s*file\(\s*['"]([^'"]+)['"]\s*\)""")


def _gradle(path: Path) -> _Workspace | None:
    try:
        text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    moved = {name.strip(":").replace(":", "/"): folder
             for name, folder in _GRADLE_PROJECT_DIR.findall(text)}
    members = [q.strip(":").replace(":", "/")
               for paren, bare in _GRADLE_INCLUDE.findall(text)
               for q in _QUOTED.findall(paren or bare)]
    return _Workspace("gradle", [moved.get(m, m) for m in members if m], []) if members else None


_MAVEN_MODULE = re.compile(r"<module>\s*([^<]+?)\s*</module>")


def _maven(path: Path) -> _Workspace | None:
    try:
        text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8-sig"), flags=re.DOTALL)
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    modules = _MAVEN_MODULE.findall(text)
    return _Workspace("maven", modules, []) if modules else None


# Every declaration a folder holds is read; their members are unioned.
_DECLARATIONS = (
    ("Cargo.toml", _cargo),
    ("go.work", _go),
    ("pnpm-workspace.yaml", _pnpm),
    ("package.json", _npm),
    ("settings.gradle.kts", _gradle),
    ("settings.gradle", _gradle),
    ("pom.xml", _maven),
    ("pyproject.toml", _uv),
)


def _workspaces(root: Path, folder: str, names: frozenset[str]) -> tuple[list[_Workspace], list[str]]:
    """Every workspace ``folder`` declares (a Cargo workspace with npm bindings declares
    two), and the repo-relative paths of the manifests there that cannot be parsed."""
    found: list[_Workspace] = []
    broken: list[str] = []
    for name, parse in _DECLARATIONS:
        if name in names:
            try:
                ws = parse(root / folder / name)
            except _Unreadable:
                broken.append(posixpath.normpath(posixpath.join(folder, name)))
                continue
            if ws is not None and all(w.ecosystem != ws.ecosystem for w in found):
                found.append(ws)
    return found, broken


# What makes a manifest a package rather than tooling. A monorepo root package.json
# usually has a name and devDependencies, so neither counts: a package body does.
_PACKAGE_KEYS = {
    "pyproject.toml": ("project", "tool.poetry"),
    "package.json": ("main", "module", "exports", "bin", "dependencies", "peerDependencies"),
}


def _tooling_only(root: Path, folder: str, names: frozenset[str]) -> bool:
    """True when every manifest in ``folder`` is a ``pyproject.toml`` or ``package.json``
    that declares no package: a root ``[tool.ruff]``, or a ``package.json`` with a name and
    devDependencies but no entry point or runtime dependency."""
    if not names or not names <= _PACKAGE_KEYS.keys():
        return False
    for name in names:
        text = (root / folder / name).read_text(encoding="utf-8-sig", errors="replace")
        try:
            data: Any = json.loads(text) if name == "package.json" else tomllib.loads(text)
        except (ValueError, tomllib.TOMLDecodeError):
            return False
        for key in _PACKAGE_KEYS[name]:
            node = data
            for part in key.split("."):
                node = node.get(part) if isinstance(node, Mapping) else None
            if node is not None:
                return False
    return True


# -- member expansion ---------------------------------------------------------

def _join(folder: str, pattern: str) -> str | None:
    """``pattern`` under ``folder`` as a repo-relative path, or None outside the repo."""
    joined = posixpath.normpath(posixpath.join(folder, pattern.strip().replace("\\", "/")))
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    return joined


def _glob_match(pattern: str, path: str) -> bool:
    """``fnmatch`` per path segment; ``**`` matches any number of segments."""
    def walk(pat: list[str], parts: list[str]) -> bool:
        if not pat:
            return not parts
        if pat[0] == "**":
            return any(walk(pat[1:], parts[i:]) for i in range(len(parts) + 1))
        return bool(parts) and fnmatch.fnmatchcase(parts[0], pat[0]) and walk(pat[1:], parts[1:])
    return walk(pattern.split("/"), path.split("/"))


def _expand(folder: str, ws: _Workspace, dirs: frozenset[str],
            manifest_dirs: Mapping[str, frozenset[str]]) -> tuple[list[str], list[SkippedUnit], bool]:
    """The member folders below ``folder``, the members skipped, and whether ``folder``
    itself is a member (``go.work`` ``use .``, a Cargo ``[package]`` or uv ``[project]`` root)."""
    members: set[str] = set()
    skipped: list[SkippedUnit] = []
    for raw in ws.members:
        joined = _join(folder, raw)
        if joined is None:
            skipped.append({"path": raw, "problem": "outside_repo"})
        elif _GLOB_CHARS & set(joined):
            matched = {d for d in manifest_dirs if _glob_match(joined, d)}
            if not matched - {folder}:
                skipped.append({"path": joined, "problem": "no_match"})
            members.update(matched)
        elif joined in dirs:
            members.add(joined)
        else:
            skipped.append({"path": joined, "problem": "missing"})
    excluded = [j for j in (_join(folder, e) for e in ws.excludes) if j is not None]
    kept = sorted(m for m in members
                  if m != folder and not any(_glob_match(e, m) for e in excluded))
    return kept, skipped, ws.root_member or folder in members


# -- discovery ----------------------------------------------------------------

def _parents(rel: str) -> Iterable[str]:
    parent = posixpath.dirname(rel)
    while parent:
        yield parent
        parent = posixpath.dirname(parent)
    yield "."


def _is_under(path: str, folder: str) -> bool:
    return folder == "." or path == folder or path.startswith(folder + "/")


def _kept_files(repo_root: Path, excludes: tuple[set[str], list[str]] | None) -> list[str]:
    extra_dirs, extra_patterns = excludes if excludes is not None else (set(), [])
    out: list[str] = []
    for rel in sorted(repo_files(repo_root)):
        parts = PurePosixPath(rel).parts
        if any(p in EXCLUDE_DIRS for p in parts[:-1]):
            continue
        if is_user_excluded(Path(rel), extra_dirs, extra_patterns):
            continue
        out.append(rel)
    return out


def _ecosystem(names: frozenset[str]) -> Ecosystem:
    return next(eco for name, eco in MANIFESTS if name in names)


class _Discovery(NamedTuple):
    units: list[UnitEntry]
    aggregators: list[AggregatorEntry]
    skipped: list[SkippedUnit]
    unreadable: list[str]


class _Walk:
    """The discovery state over one file list; ``discover_units`` drives it."""

    def __init__(self, repo_root: Path, files: list[str], components: Iterable[str] = ()) -> None:
        self.root = repo_root
        component_dirs = frozenset(components)
        self.components = component_dirs  # units already: a workspace listing one adds nothing
        self.dirs = frozenset(p for rel in files for p in _parents(rel))
        by_dir: dict[str, set[str]] = {}
        for rel in files:
            if posixpath.basename(rel) in MANIFEST_NAMES:
                by_dir.setdefault(posixpath.dirname(rel) or ".", set()).add(posixpath.basename(rel))
        self.manifest_dirs = {d: frozenset(n) for d, n in by_dir.items()}
        # a build script (build.gradle.kts) is a manifest, never a source file
        sources = [rel for rel in files if PurePosixPath(rel).suffix.lower() in CODE_EXTENSIONS
                   and posixpath.basename(rel) not in MANIFEST_NAMES]
        self.source_dirs = frozenset(p for rel in sources for p in _parents(rel))
        # the manifest folder nearest each source file: a folder claiming nothing owns these
        # (a component folder owns its files, so a manifest folder above it is no unit of them)
        self.owning = frozenset(next(p for p in _parents(rel)
                                     if p in self.manifest_dirs or p in component_dirs or p == ".")
                                for rel in sources)
        self.found = _Discovery([], [], [], [])
        # unit folders: nothing below one is a new candidate (component units are claimed first)
        self.claimed: list[str] = list(component_dirs)
        self.visited: set[str] = set()  # handled candidates (a nested aggregator claims nothing)
        self.leaves: dict[str, list[str]] = {}  # folder -> its manifests skipped as unparseable_leaf

    def _unit(self, path: str, origin: Origin, eco: Ecosystem | None, workspace: str | None) -> None:
        self.found.units.append({"path": path, "kind": "package", "origin": origin, "ecosystem": eco,
                                 "workspace": workspace, "name": None})

    def _skip(self, path: str, problem: str) -> None:
        self.found.skipped.append({"path": path, "problem": problem})

    def own_files(self, path: str, origin: Origin, eco: Ecosystem | None, workspace: str | None,
                  empty: str) -> None:
        """A unit of the source files no nested manifest owns; it claims nothing."""
        if path in self.owning:
            self._unit(path, origin, eco, workspace)
        else:
            self._skip(path, empty)

    def add(self, path: str, origin: Origin, eco: Ecosystem | None, workspace: str | None) -> None:
        # A folder above units already claimed (a non-member manifest over workspace
        # members) is a unit of its own files only and claims nothing, so units never nest.
        if any(c != path and _is_under(c, path) for c in self.claimed):
            self.own_files(path, origin, eco, workspace, "manifest_only")
            return
        self.claimed.append(path)
        if path in self.source_dirs:
            self._unit(path, origin, eco, workspace)
        else:
            self._skip(path, "manifest_only")

    def visit(self, folder: str, origin: Origin, workspace: str | None,
              member_eco: Ecosystem | None = None) -> None:
        """One candidate: an aggregator when it declares a workspace, else a unit.

        A workspace member (``member_eco`` set) keeps its workspace's ecosystem, and its
        Maven ``<modules>`` are not expanded."""
        self.visited.add(folder)
        names = self.manifest_dirs.get(folder, frozenset())
        declared, broken = _workspaces(self.root, folder, names)
        if broken and (folder == "." or origin == "workspace_member" or declared
                       or any(posixpath.basename(b) in WORKSPACE_ONLY for b in broken)):
            # the members are unknown: an aggregator of unknown size, claimed whole
            self.found.unreadable.extend(broken)
            self.found.aggregators.append({"path": folder, "member_count": None,
                                           "ecosystem": dict(MANIFESTS)[posixpath.basename(broken[0])]})
            self.claimed.append(folder)
            return
        for path in broken:  # a leaf (a broken fixture): it cannot change the unit set
            self._skip(path, "unparseable_leaf")
        if broken:
            self.leaves[folder] = broken
        if member_eco is not None:
            declared = [ws for ws in declared if ws.ecosystem != "maven"]
        expanded = [(ws, *_expand(folder, ws, self.dirs, self.manifest_dirs)) for ws in declared]
        for _, _, member_skips, _ in expanded:
            self.found.skipped.extend(member_skips)
        eco = member_eco or (_ecosystem(names) if names else None)
        if not any(members or root for _, members, _, root in expanded):
            # only the root: a nested package.json with sources is an app, entry point or not
            if folder == "." and _tooling_only(self.root, folder, names):
                self.own_files(folder, origin, eco, workspace, "tooling_only")
            else:  # no workspace, or one that lists no member: a plain unit
                self.add(folder, origin, eco, workspace)
            return
        self.aggregate(folder, expanded)

    def unleaf(self, folder: str) -> None:
        """Move ``folder``'s leaf skips to ``unreadable``: a workspace lists it after all."""
        broken = self.leaves.pop(folder)
        self.found.skipped[:] = [s for s in self.found.skipped
                                 if not (s["problem"] == "unparseable_leaf" and s["path"] in broken)]
        self.found.unreadable.extend(broken)

    def aggregate(self, folder: str,
                  expanded: list[tuple[_Workspace, list[str], list[SkippedUnit], bool]]) -> None:
        for ws, members, _, root in expanded:
            self.found.aggregators.append({"path": folder, "ecosystem": ws.ecosystem,
                                           "member_count": len(members) + root})
        if any(root for *_, root in expanded) or folder in self.owning:
            # the root package (Cargo [package], uv [project], go.work `use .`, or an npm or
            # pnpm root with sources of its own) is a unit of its own files
            self.own_files(folder, "workspace_member", expanded[0][0].ecosystem, folder,
                           "manifest_only")
        for ws, members, _, _ in expanded:
            for member in members:
                if member in self.components:  # a skill a workspace lists is that skill's unit
                    continue
                if member in self.leaves:
                    # a member shallower than its workspace was read as a leaf first
                    self.unleaf(member)
                # visited too: two workspaces that list each other would otherwise recurse
                if member in self.visited or any(_is_under(member, c) for c in self.claimed):
                    self._skip(member, "claimed")
                else:
                    self.visit(member, "workspace_member", folder, ws.ecosystem)


def discover_units(repo_root: Path, files: Iterable[str]) -> _Discovery:
    """The units of ``files`` (repo-relative POSIX paths) by the rules in the module docstring."""
    files = list(files)
    components = discover_components(files, frozenset(p for rel in files for p in _parents(rel)))
    walk = _Walk(repo_root, files, (c.path for c in components.units))
    for comp in components.units:
        names = walk.manifest_dirs.get(comp.path)
        walk.found.units.append({"path": comp.path, "kind": comp.kind, "origin": "component",
                                 "ecosystem": _ecosystem(names) if names else None,
                                 "workspace": None, "name": None})
    walk.found.skipped.extend({"path": path, "problem": "claimed"} for path in components.claimed)
    # a build-manifest folder inside a component is part of it, listed so the claim is visible
    # (a root SKILL.md over a monorepo's packages); test data below it is not listed
    walk.found.skipped.extend(
        {"path": folder, "problem": "claimed"} for folder in sorted(walk.manifest_dirs)
        if folder not in components.claimed
        and any(c.path != folder and _is_under(folder, c.path) for c in components.units)
        and is_production(posixpath.join(folder, sorted(walk.manifest_dirs[folder])[0])))
    depth = {d: (0 if d == "." else d.count("/") + 1) for d in walk.manifest_dirs}
    for folder in sorted(walk.manifest_dirs, key=lambda d: (depth[d], d)):
        if folder not in walk.visited and not any(_is_under(folder, c) for c in walk.claimed):
            walk.visit(folder, "manifest", None)
    found = walk.found
    found.units.sort(key=lambda u: u["path"])
    found.unreadable.sort()
    return found


def declared_units(config: Mapping[str, Any], dirs: frozenset[str],
                   skill_dirs: frozenset[str] = frozenset()) -> tuple[list[UnitEntry], list[SkippedUnit]]:
    """``[[units]]`` entries of a parsed ``.assess/config.toml``, and those skipped with why.

    A declared folder holding a ``SKILL.md`` (``skill_dirs``) is of kind ``skill``, else ``package``."""
    if SECTION not in config:
        return [], []
    raw = config[SECTION]
    if not isinstance(raw, list):
        return [], [{"path": None, "problem": "not_a_list"}]
    units: list[UnitEntry] = []
    skipped: list[SkippedUnit] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            skipped.append({"path": None, "problem": "not_a_table"})
            continue
        path = _clean_rel(entry.get("path"), allow_root=True)
        if path is None:
            skipped.append({"path": None, "problem": "no_path"})
        elif path not in dirs:
            skipped.append({"path": path, "problem": "missing"})
        elif path in seen:
            skipped.append({"path": path, "problem": "duplicate"})
        else:
            seen.add(path)
            name = entry.get("name")
            units.append({"path": path, "kind": "skill" if path in skill_dirs else "package",
                          "origin": "config", "ecosystem": None, "workspace": None,
                          "name": name.strip() if isinstance(name, str) and name.strip() else None})
    return units, skipped


def scan_units(repo_root: Path, excludes: tuple[set[str], list[str]] | None = None) -> UnitsBlock:
    """The run context's ``units`` block: declared ``[[units]]`` when any is valid, else discovered."""
    files = _kept_files(repo_root, excludes)
    dirs = frozenset(p for rel in files for p in _parents(rel))
    skill_dirs = frozenset(posixpath.dirname(rel) or "." for rel in files
                           if posixpath.basename(rel) == SKILL_FILE)
    declared, declared_skips = declared_units(load_config(repo_root), dirs, skill_dirs)
    for skip in declared_skips:
        print(f"warning: .assess/config.toml [[units]] entry skipped ({skip['problem']}): "
              f"{skip['path'] or '(no path)'}", file=sys.stderr)
    if declared:
        return {"source": "config", "unit_count": len(declared), "unit_count_reason": None,
                "units": declared, "aggregators": [], "aggregator_count": 0, "skipped": declared_skips,
                "unreadable_manifests": []}
    found = discover_units(repo_root, files)
    # an unreadable manifest that could declare a workspace leaves its members unknown
    known = not found.unreadable
    return {"source": "discovered", "unit_count": len(found.units) if known else None,
            "unit_count_reason": None if known else UNREADABLE_REASON, "units": found.units,
            "aggregators": found.aggregators, "aggregator_count": len(found.aggregators),
            "skipped": declared_skips + found.skipped, "unreadable_manifests": found.unreadable}
