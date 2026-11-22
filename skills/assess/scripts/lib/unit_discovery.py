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
   Members are expanded one level: a member that is itself an aggregator (a
   Maven ``pom`` module with ``<modules>``) is one unit, as the Helidon spike on
   #800 counted its top-level areas. Workspace declarations read:

   - Cargo: ``[workspace] members`` and ``exclude`` in ``Cargo.toml``;
   - pnpm: ``packages`` in ``pnpm-workspace.yaml`` (``!`` excludes);
   - npm and Yarn: ``workspaces`` in ``package.json``, a list or ``{packages}``;
   - Go: ``use`` in ``go.work``;
   - uv: ``[tool.uv.workspace] members`` and ``exclude`` in ``pyproject.toml``;
   - Gradle: ``include`` in ``settings.gradle`` or ``settings.gradle.kts``;
   - Maven: ``<module>`` entries in ``pom.xml``.

   A member pattern may be a glob (``crates/*``, ``packages/**``); a glob match
   counts only when the folder holds a manifest. A literal member that no
   tracked file sits under is skipped as ``missing``, and one that resolves
   outside the repository as ``outside_repo``.
3. A unit with no source file below it (``doc_graph.CODE_EXTENSIONS``) is a
   manifest-only aggregator (a BOM, a parent POM) and is skipped as
   ``manifest_only``.

A workspace manifest that cannot be parsed is named in ``unreadable_manifests``
and its folder is a plain unit: the manifest is there, its members are unknown.
A repository with no build manifest has no units (``unit_count`` 0).

**Declared units.** ``[[units]]`` in ``.assess/config.toml`` replaces discovery::

    [[units]]
    path = "services/billing"   # repo-relative folder, required; "." is the root
    name = "Billing"            # optional display name

An entry is skipped, with a ``PROBLEMS`` code and a warning on stderr, when it
is not a table, has no usable path, names a folder with no tracked file, or
repeats a path. Declared units are used when at least one entry is valid;
otherwise discovery runs and the skipped entries are still listed.

Counts-only output drops every path and name (``checks/core/units.toml``): it
keeps the counts, the origin and ecosystem tokens and the skip codes. Pure apart
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

Ecosystem = Literal["cargo", "pnpm", "npm", "go", "uv", "gradle", "maven", "python"]
Origin = Literal["manifest", "workspace_member", "config"]

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
SOURCES = ("discovered", "config")
# Why a unit candidate or a [[units]] entry was skipped, as SkippedUnit.problem holds it.
PROBLEMS = (
    "manifest_only", "missing", "outside_repo", "not_a_list", "not_a_table", "no_path",
    "duplicate",
)
_GLOB_CHARS = frozenset("*?[")


class UnitEntry(TypedDict):
    """One unit: its folder, how it was found, and its workspace when it is a member."""

    path: str
    origin: Origin
    ecosystem: Ecosystem | None  # None for a declared unit
    workspace: str | None  # the aggregator folder of a workspace member
    name: str | None  # a declared unit's display name


class AggregatorEntry(TypedDict):
    """A workspace root: not a unit itself; ``member_count`` units were declared by it."""

    path: str
    ecosystem: Ecosystem
    member_count: int


class SkippedUnit(TypedDict):
    """A candidate or declared unit left out, with a ``PROBLEMS`` code."""

    path: str | None
    problem: str


class UnitsBlock(TypedDict):
    """The run context's ``units`` block (#862)."""

    source: Literal["discovered", "config"]
    unit_count: int
    units: list[UnitEntry]
    aggregators: list[AggregatorEntry]
    aggregator_count: int
    skipped: list[SkippedUnit]
    unreadable_manifests: list[str]


class _Workspace(NamedTuple):
    ecosystem: Ecosystem
    members: list[str]  # patterns relative to the aggregator folder
    excludes: list[str]


class _Unreadable(Exception):
    """A workspace manifest that exists but cannot be parsed."""


# -- workspace declarations ---------------------------------------------------

def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _toml(path: Path) -> Mapping[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e


def _cargo(path: Path) -> _Workspace | None:
    ws = _toml(path).get("workspace")
    if not isinstance(ws, Mapping):
        return None
    return _Workspace("cargo", _strings(ws.get("members")), _strings(ws.get("exclude")))


def _uv(path: Path) -> _Workspace | None:
    tool = _toml(path).get("tool")
    uv = tool.get("uv") if isinstance(tool, Mapping) else None
    ws = uv.get("workspace") if isinstance(uv, Mapping) else None
    if not isinstance(ws, Mapping):
        return None
    return _Workspace("uv", _strings(ws.get("members")), _strings(ws.get("exclude")))


def _split_negated(patterns: Iterable[str]) -> tuple[list[str], list[str]]:
    members = [p for p in patterns if not p.startswith("!")]
    return members, [p[1:] for p in patterns if p.startswith("!")]


def _npm(path: Path) -> _Workspace | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    if not isinstance(data, Mapping) or "workspaces" not in data:
        return None
    ws = data["workspaces"]
    patterns = _strings(ws.get("packages") if isinstance(ws, Mapping) else ws)
    return _Workspace("npm", *_split_negated(patterns))


_YAML_ITEM = re.compile(r"""^\s*-\s*['"]?([^'"#]+?)['"]?\s*(?:#.*)?$""")


def _pnpm(path: Path) -> _Workspace:
    """``packages:`` list items, read line by line (no YAML dependency in the core)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    patterns: list[str] = []
    inside = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace() and not line.startswith("-"):
            inside = line.split("#")[0].strip() == "packages:"
            continue
        match = _YAML_ITEM.match(line) if inside else None
        if match:
            patterns.append(match.group(1).strip())
    return _Workspace("pnpm", *_split_negated(patterns))


_GO_USE = re.compile(r"^\s*use\s+(?:\(\s*(?P<block>[^)]*)\)|(?P<one>\S+))", re.MULTILINE)


def _go(path: Path) -> _Workspace:
    try:
        text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    members: list[str] = []
    for match in _GO_USE.finditer(text):
        members.extend((match.group("block") or match.group("one") or "").split())
    return _Workspace("go", [m.strip('"') for m in members], [])


_GRADLE_INCLUDE = re.compile(r"\binclude\b(?!Build)\s*\(?([^\n)]*)")
_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")


def _gradle(path: Path) -> _Workspace | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    members = [q.strip(":").replace(":", "/")
               for args in _GRADLE_INCLUDE.findall(text) for q in _QUOTED.findall(args)]
    return _Workspace("gradle", [m for m in members if m], []) if members else None


_MAVEN_MODULE = re.compile(r"<module>\s*([^<]+?)\s*</module>")


def _maven(path: Path) -> _Workspace | None:
    try:
        text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    except (UnicodeDecodeError, OSError) as e:
        raise _Unreadable(str(path)) from e
    modules = _MAVEN_MODULE.findall(text)
    return _Workspace("maven", modules, []) if modules else None


# Checked in order; the first declaration found makes the folder an aggregator.
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


def _workspace(root: Path, folder: str, names: frozenset[str]) -> _Workspace | None:
    """The workspace ``folder`` declares, or None; ``_Unreadable`` names the manifest by its
    repo-relative path when one cannot be parsed."""
    for name, parse in _DECLARATIONS:
        if name in names:
            try:
                ws = parse(root / folder / name)
            except _Unreadable as e:
                raise _Unreadable(posixpath.normpath(posixpath.join(folder, name))) from e
            if ws is not None:
                return ws
    return None


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
            manifest_dirs: Mapping[str, frozenset[str]]) -> tuple[list[str], list[SkippedUnit]]:
    members: set[str] = set()
    skipped: list[SkippedUnit] = []
    for raw in ws.members:
        joined = _join(folder, raw)
        if joined is None:
            skipped.append({"path": raw, "problem": "outside_repo"})
        elif _GLOB_CHARS & set(joined):
            members.update(d for d in manifest_dirs if _glob_match(joined, d))
        elif joined in dirs:
            members.add(joined)
        else:
            skipped.append({"path": joined, "problem": "missing"})
    excluded = [j for j in (_join(folder, e) for e in ws.excludes) if j is not None]
    kept = sorted(m for m in members
                  if m != folder and not any(_glob_match(e, m) for e in excluded))
    return kept, skipped


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


def discover_units(repo_root: Path, files: Iterable[str]) -> _Discovery:
    """The units of ``files`` (repo-relative POSIX paths) by the rules in the module docstring."""
    files = list(files)
    dirs = frozenset(p for rel in files for p in _parents(rel))
    by_dir: dict[str, set[str]] = {}
    for rel in files:
        name = posixpath.basename(rel)
        if name in MANIFEST_NAMES:
            by_dir.setdefault(posixpath.dirname(rel) or ".", set()).add(name)
    manifest_dirs = {d: frozenset(n) for d, n in by_dir.items()}
    source_dirs = frozenset(
        p for rel in files if PurePosixPath(rel).suffix.lower() in CODE_EXTENSIONS
        for p in _parents(rel)
    )

    units: list[UnitEntry] = []
    aggregators: list[AggregatorEntry] = []
    skipped: list[SkippedUnit] = []
    unreadable: list[str] = []
    claimed: list[str] = []  # unit folders: nothing below one is a new candidate

    def add(path: str, origin: Origin, eco: Ecosystem | None, workspace: str | None) -> None:
        claimed.append(path)
        if path not in source_dirs:
            skipped.append({"path": path, "problem": "manifest_only"})
            return
        units.append({"path": path, "origin": origin, "ecosystem": eco,
                      "workspace": workspace, "name": None})

    depth = {d: (0 if d == "." else d.count("/") + 1) for d in manifest_dirs}
    for folder in sorted(manifest_dirs, key=lambda d: (depth[d], d)):
        if any(_is_under(folder, c) for c in claimed):
            continue
        names = manifest_dirs[folder]
        try:
            ws = _workspace(repo_root, folder, names)
        except _Unreadable as e:
            unreadable.append(str(e))
            ws = None
        if ws is None:
            add(folder, "manifest", _ecosystem(names), None)
            continue
        members, member_skips = _expand(folder, ws, dirs, manifest_dirs)
        skipped.extend(member_skips)
        aggregators.append({"path": folder, "ecosystem": ws.ecosystem,
                            "member_count": len(members)})
        for member in members:
            if any(_is_under(member, c) for c in claimed):
                continue
            add(member, "workspace_member", ws.ecosystem, folder)

    units.sort(key=lambda u: u["path"])
    return _Discovery(units, aggregators, skipped, sorted(unreadable))


def declared_units(config: Mapping[str, Any], dirs: frozenset[str]) -> tuple[list[UnitEntry], list[SkippedUnit]]:
    """``[[units]]`` entries of a parsed ``.assess/config.toml``, and those skipped with why."""
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
            units.append({"path": path, "origin": "config", "ecosystem": None, "workspace": None,
                          "name": name.strip() if isinstance(name, str) and name.strip() else None})
    return units, skipped


def scan_units(repo_root: Path, excludes: tuple[set[str], list[str]] | None = None) -> UnitsBlock:
    """The run context's ``units`` block: declared ``[[units]]`` when any is valid, else discovered."""
    files = _kept_files(repo_root, excludes)
    dirs = frozenset(p for rel in files for p in _parents(rel))
    declared, declared_skips = declared_units(load_config(repo_root), dirs)
    for skip in declared_skips:
        print(f"warning: .assess/config.toml [[units]] entry skipped ({skip['problem']}): "
              f"{skip['path'] or '(no path)'}", file=sys.stderr)
    if declared:
        return {"source": "config", "unit_count": len(declared), "units": declared,
                "aggregators": [], "aggregator_count": 0, "skipped": declared_skips,
                "unreadable_manifests": []}
    found = discover_units(repo_root, files)
    return {"source": "discovered", "unit_count": len(found.units), "units": found.units,
            "aggregators": found.aggregators, "aggregator_count": len(found.aggregators),
            "skipped": declared_skips + found.skipped, "unreadable_manifests": found.unreadable}
