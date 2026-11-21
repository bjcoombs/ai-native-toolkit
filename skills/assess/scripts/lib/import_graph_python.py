"""Python import-graph builder: grimp over discovered packages (issue #708).

Implements the ``lib.import_graph`` builder interface for Python. A package is a
top-level directory holding an ``__init__.py``; a module is a dotted name; a
package's front doors are its ``__init__`` modules; a module's public surface is
its public top-level ``def`` / ``class`` count, read with ``ast``.

Packages are resolved **by path, never by name**. grimp's default package
finder asks ``importlib.util.find_spec``, which answers from ``sys.modules``
first: when the assessed tree has a package called ``lib`` (or ``json``, or
``networkx``), the running ``/assess`` has already imported a module of that
name and grimp would graph *that* copy instead of the target's. So each build
swaps in a finder that maps a name to the directory discovery found, and
``sys.path`` / ``sys.modules`` are never touched.

A name held by two discovered packages (``a/lib`` and ``b/lib``) cannot share
one grimp graph, so packages are split across builds: each copy is graphed
under its repo-relative path (``a/lib``, ``b/lib``) and listed in
``name_collisions``. Every import between builds is dropped, not only an
import of the colliding name: grimp treats a package outside the current build
as external. For the colliding name the drop is also the honest answer, since
which copy wins depends on a runtime ``sys.path`` no static read can know; for
a unique name it is a loss, so with collisions present the A1 footprints and
A3 front-door ratio can be understated. A root holding a colliding name is
always a build of its own (``_partition_builds``), so an import of that name
from another root is never pinned to whichever copy sorts first.
"""
from __future__ import annotations

import ast
import keyword
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

try:  # grimp is the Python builder's tool; degrade rather than crash if absent.
    import grimp
    from grimp.application.config import settings as _grimp_settings

    _GRIMP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a broken env
    grimp = None  # type: ignore[assignment]  # grimp is typed; None stands in for the missing module
    _grimp_settings = None  # type: ignore[assignment]  # as above: absent module
    _GRIMP_AVAILABLE = False

from lib.import_graph import EXCLUDE_DIRS, GraphUnavailable, ModuleGraph, NameCollision

GRIMP_MISSING = "grimp not installed; static structure not assessed"


def _is_excluded(path: Path, repo_root: Path, extra: set[str]) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return True
    return any(part in EXCLUDE_DIRS or part in extra for part in rel.parts)


def discover_packages(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None,
) -> list[Path]:
    """Return the top-level importable package directories under repo_root.

    A package directory contains an ``__init__.py``. We keep only *top-level*
    packages -- a directory whose parent is not itself a package -- because
    grimp is given the package root and walks down from there. ``repo_root``
    itself counts if it is a package (the integration case: pointing the
    analysis straight at ``scripts/lib``).
    """
    repo_root = repo_root.resolve()
    extra = extra_exclude_dirs or set()
    init_dirs: set[Path] = set()
    # repo_root itself may be a package.
    if (repo_root / "__init__.py").is_file():
        init_dirs.add(repo_root)
    for init in repo_root.rglob("__init__.py"):
        if not init.is_file():
            continue
        if _is_excluded(init.parent, repo_root, extra):
            continue
        init_dirs.add(init.parent.resolve())
    # Keep only roots: a package whose parent is also a package is a subpackage.
    # A directory no import statement can name (``my-pkg``, ``class``) is not a
    # package whatever files it holds.
    return sorted(
        d for d in init_dirs
        if d.parent not in init_dirs and _is_importable_name(d.name)
    )


def _is_importable_name(name: str) -> bool:
    """True when an ``import`` statement could name a package called ``name``."""
    return name.isidentifier() and not keyword.iskeyword(name)


class _PathPackageFinder:
    """grimp package finder that answers from discovered paths, not imports.

    Stands in for grimp's ``ImportLibPackageFinder``, whose ``find_spec``
    lookup returns whatever module of that name is already imported. The port
    changed shape in grimp 3.14: 3.0-3.13 call ``determine_package_directory``
    (one path), 3.14+ call ``determine_package_directories`` (a set). Both are
    implemented so every grimp the ``>=3.0`` floor admits works.
    """

    def __init__(self, dirs: dict[str, Path]) -> None:
        self._dirs = dirs

    def determine_package_directory(
        self, package_name: str, file_system: object,
    ) -> str:
        del file_system  # part of grimp's port signature; unused here
        directory = self._dirs.get(package_name)
        if directory is None:
            raise ValueError(f"package {package_name!r} was not discovered")
        return str(directory)

    def determine_package_directories(
        self, package_name: str, file_system: object,
    ) -> set[str]:
        return {self.determine_package_directory(package_name, file_system)}


@contextmanager
def _path_package_finder(dirs: dict[str, Path]) -> Iterator[None]:
    """Point grimp's package finder at ``dirs`` for one build, then restore it.

    Callers reach this only after the builder's grimp-available check.
    """
    original = _grimp_settings.PACKAGE_FINDER
    _grimp_settings.configure(PACKAGE_FINDER=_PathPackageFinder(dirs))
    try:
        yield
    finally:
        _grimp_settings.configure(PACKAGE_FINDER=original)


def _module_file(module: str, roots: dict[str, Path]) -> Path | None:
    """Resolve a graph module name to its source file via its package root.

    ``roots`` maps a graph-level top name (``lib``, or a path-qualified
    ``a/lib`` for a colliding name) to its package directory; the rest of the
    dotted name walks down from there.
    """
    top, _, rest = module.partition(".")
    root = roots.get(top)
    if root is None:
        return None
    if not rest:
        init = root / "__init__.py"
        return init if init.is_file() else None
    base = root.joinpath(*rest.split("."))
    candidate = base.parent / f"{base.name}.py"
    if candidate.is_file():
        return candidate
    pkg_init = base / "__init__.py"
    if pkg_init.is_file():
        return pkg_init
    return None


def _public_surface(path: Path | None) -> int:
    """Count public top-level definitions (the API a module exposes).

    Public = a module-level ``def`` / ``async def`` / ``class`` whose name does
    not start with ``_``. This is the surface a dependent must comprehend to
    use the module -- the contract, not the implementation.
    """
    if path is None:
        return 0
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return 0
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                count += 1
    return count


@dataclass
class ImportMap:
    """The direct-import graph of the discovered packages, merged across builds.

    ``modules`` and ``imports`` use graph-level names: plain dotted names, with
    the top component path-qualified (``a/lib.x``) only for a colliding name.
    ``roots`` maps each graph-level top name to its package directory.
    """

    modules: list[str] = field(default_factory=list)
    imports: dict[str, set[str]] = field(default_factory=dict)
    roots: dict[str, Path] = field(default_factory=dict)
    package_names: list[str] = field(default_factory=list)
    name_collisions: list[NameCollision] = field(default_factory=list)


def _partition_builds(package_dirs: list[Path]) -> list[list[Path]]:
    """Split packages into builds whose top-level names are unique.

    Packages sharing a parent directory (one ``sys.path`` root) always share a
    build, so their imports of each other resolve. Roots holding no colliding
    name all share one build, where every name is unique by definition; a repo
    with no collisions is that single build, exactly as before. A root holding a
    colliding name gets a build of its own, so no other root's import of that
    name can land on one copy just because its path sorts first: the import is
    external to every build that does not own the name, and is dropped.
    """
    counts = Counter(d.name for d in package_dirs)
    by_parent: dict[Path, list[Path]] = {}
    for d in sorted(package_dirs):
        by_parent.setdefault(d.parent, []).append(d)
    shared: list[Path] = []
    isolated: list[list[Path]] = []
    for group in by_parent.values():
        if any(counts[d.name] > 1 for d in group):
            isolated.append(group)
        else:
            shared.extend(group)
    return ([shared] if shared else []) + isolated


def _qualified_name(package_dir: Path, repo_root: Path) -> str:
    """Repo-relative path of a package, dot-free so it stays one name component."""
    try:
        rel = package_dir.relative_to(repo_root).as_posix()
    except ValueError:  # pragma: no cover - discovery only yields paths under root
        return package_dir.name
    if rel == ".":
        return package_dir.name
    return rel.replace("%", "%25").replace(".", "%2E")


def _rename(module: str, renames: dict[str, str]) -> str:
    """Swap a module name's top component for its graph-level (qualified) name."""
    top, sep, rest = module.partition(".")
    return renames.get(top, top) + sep + rest


def _add_build(
    imap: ImportMap, build: list[Path], renames: dict[str, str],
) -> None:
    """Graph one build's packages and merge them into ``imap`` under ``renames``."""
    dirs = {d.name: d for d in build}
    with _path_package_finder(dirs):
        # cache_dir=None: never write grimp's cache into the target repo.
        graph = grimp.build_graph(*sorted(dirs), cache_dir=None)
    for name, directory in dirs.items():
        imap.roots[renames[name]] = directory
    for module in graph.modules:
        imap.imports[_rename(module, renames)] = {
            _rename(dep, renames)
            for dep in graph.find_modules_directly_imported_by(module)
        }


def build_import_map(package_dirs: list[Path], repo_root: Path) -> ImportMap:
    """Build the import graph of ``package_dirs``, resolving every name by path.

    Raises whatever grimp raises on an unparseable tree; callers degrade.
    """
    repo_root = repo_root.resolve()
    counts = Counter(d.name for d in package_dirs)
    imap = ImportMap()
    for build in _partition_builds(package_dirs):
        renames = {
            d.name: _qualified_name(d, repo_root) if counts[d.name] > 1 else d.name
            for d in build
        }
        _add_build(imap, build, renames)
    imap.modules = sorted(imap.imports)
    imap.package_names = sorted(imap.roots)
    imap.name_collisions = [
        {
            "name": name,
            "paths": sorted(
                _qualified_name(d, repo_root)
                for d in package_dirs if d.name == name
            ),
        }
        for name in sorted(n for n, c in counts.items() if c > 1)
    ]
    return imap


def module_graph(imap: ImportMap) -> ModuleGraph:
    """The language-neutral :class:`ModuleGraph` of a Python ``ImportMap``.

    A module's package is its top dotted component (the package root, or its
    path-qualified name for a colliding root). Front doors are the package
    roots and every ``__init__`` module.
    """
    files: dict[str, Path] = {}
    for m in imap.modules:
        f = _module_file(m, imap.roots)
        if f is not None:
            files[m] = f
    return ModuleGraph(
        language="python",
        modules=imap.modules,
        edges=imap.imports,
        front_doors=set(imap.package_names) | {
            m for m, f in files.items() if f.name == "__init__.py"
        },
        module_file=files,
        package_of={m: m.split(".", 1)[0] for m in imap.modules},
        surfaces={m: _public_surface(files.get(m)) for m in imap.modules},
        name_collisions=imap.name_collisions,
    )


class PythonBuilder:
    """The Python :class:`lib.import_graph.ImportGraphBuilder`."""

    language = "python"

    def build(
        self, repo_root: Path, extra_exclude_dirs: set[str], scope: Path | None,
    ) -> ModuleGraph | None:
        package_dirs = discover_packages(repo_root, extra_exclude_dirs)
        if scope is not None:
            scope_abs = scope.resolve()
            package_dirs = [
                p for p in package_dirs if p.resolve().is_relative_to(scope_abs)
            ]
        if not package_dirs:
            return None
        if not _GRIMP_AVAILABLE:
            raise GraphUnavailable(GRIMP_MISSING)
        try:
            imap = build_import_map(package_dirs, repo_root)
        except Exception as e:  # pragma: no cover - grimp parse failure on odd trees
            raise GraphUnavailable(f"grimp failed to build import graph ({e})") from e
        return module_graph(imap)


PYTHON_BUILDER = PythonBuilder()
