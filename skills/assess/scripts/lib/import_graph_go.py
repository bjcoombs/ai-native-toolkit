"""Go import-graph builder: ``import`` blocks read as text (issue #710).

Implements the ``lib.import_graph`` builder interface for Go. No go tool runs:
``go list`` resolves modules and can reach the network, so the builder reads
``go.mod`` and each file's ``import`` declarations itself.

- A **module** of the graph is a Go package: a directory holding a non-test
  ``.go`` file. Go imports name packages, never files, so a file-level graph
  would have no edge to resolve.
- A module id is the package directory relative to the repository root
  (``internal/store``; ``.`` for the root). The go.mod module path
  (``example.com/org/svc``) only resolves an import to a directory and never
  appears in an id, so no module path reaches the block.
- The **package boundary** is the package itself (``package_of[m] == m``) and
  every package is a front door: an importer reaches only a package's exported
  identifiers, so Go has no burrow import and ``front_door_ratio`` is 1.0 by
  construction. Layer 2 level 2 for Go therefore reads modularity plus at least
  one cross-package import.
- The **public surface** (A1) is the package's exported top-level ``func``
  (methods excluded) and ``type`` declarations across its files.
- No ``module_file``: a package is several files, so A1 size reads 0 and the
  footprint is the surface terms alone; structure-drift tier 1, whose static
  lens pairs files, reads unavailable (``no_static_file_map``).

A package belongs to the nearest ``go.mod`` at or above it; a package with none
is not graphed, and a repository with no ``go.mod`` returns ``None`` (the
dominant-language reason, ``no_import_graph_go``, then applies). Test files,
``testdata``, and directories starting with ``.`` or ``_`` are skipped, as the
go tool skips them. Build constraints are not evaluated: every non-test file
counts. A file that cannot be read is skipped. The builder has no tool to miss,
so it never raises ``GraphUnavailable``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from lib.import_graph import EXCLUDE_DIRS, ModuleGraph

# A Go string, raw string or rune literal (kept), or a comment (blanked).
_LEXEME = re.compile(
    r'"(?:\\.|[^"\\\n])*"|`[^`]*`|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?\*/',
    re.DOTALL,
)
_TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|`[^`]*`|\w+|\S', re.UNICODE)
_DECL = re.compile(r"^(func|type)[ \t]+(\(?)[ \t]*(\w*)", re.MULTILINE | re.UNICODE)
_GROUP_MEMBER = re.compile(r"^\s+(\w+)", re.UNICODE)


def strip_comments(text: str) -> str:
    """``text`` with every comment blanked; strings and line breaks kept."""
    def blank(match: re.Match[str]) -> str:
        lexeme = match.group(0)
        if lexeme.startswith("//"):
            return ""
        if lexeme.startswith("/*"):
            return "\n" * lexeme.count("\n") or " "
        return lexeme
    return _LEXEME.sub(blank, text)


def _unquote(token: str) -> str | None:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in '"`':
        return token[1:-1]
    return None


def parse_imports(text: str) -> list[str]:
    """The import paths a Go source file declares, in order.

    Reads from the package clause to the first declaration that is not an
    ``import``: single (``import "a"``), grouped (``import ( ... )``), aliased
    (``import x "a"``, ``_``, ``.``), raw-string and ``;``-separated forms.
    """
    tokens = _TOKEN.findall(strip_comments(text))
    try:
        i = tokens.index("package") + 2  # the clause and the package name
    except ValueError:
        return []
    paths: list[str] = []
    while i < len(tokens):
        if tokens[i] == ";":
            i += 1
            continue
        if tokens[i] != "import":
            break
        i += 1
        if i < len(tokens) and tokens[i] == "(":
            i += 1
            while i < len(tokens) and tokens[i] != ")":
                path = _unquote(tokens[i])
                if path is not None:
                    paths.append(path)
                i += 1
            i += 1
            continue
        while i < len(tokens):
            path = _unquote(tokens[i])
            i += 1
            if path is not None:
                paths.append(path)
                break
    return paths


def exported_surface(text: str) -> int:
    """Exported top-level ``func`` (not method) and ``type`` declarations."""
    stripped = strip_comments(text)
    count = 0
    for match in _DECL.finditer(stripped):
        keyword, paren, name = match.groups()
        if keyword == "func":
            if not paren and name[:1].isupper():
                count += 1
        elif not paren:
            count += name[:1].isupper()
        else:
            count += _exported_group_members(stripped[match.end():])
    return count


def _exported_group_members(rest: str) -> int:
    """Exported names in a ``type ( ... )`` group, read up to its ``)``."""
    count = 0
    depth = 0
    for line in rest.splitlines():
        if depth == 0 and line.strip().startswith(")"):
            break
        member = _GROUP_MEMBER.match(line) if depth == 0 else None
        if member is not None and member.group(1)[:1].isupper():
            count += 1
        depth += line.count("{") - line.count("}")
    return count


def module_path(go_mod: str) -> str | None:
    """The ``module`` directive's path in a go.mod's text, or None."""
    lines = [line.strip() for line in strip_comments(go_mod).splitlines()]
    lines = [line for line in lines if line]
    for i, line in enumerate(lines):
        keyword, *rest = line.replace("(", " ( ", 1).split(None, 1)
        if keyword != "module":
            continue
        value = rest[0].strip() if rest else ""
        if value == "(":  # the block form: module ( path )
            value = lines[i + 1] if i + 1 < len(lines) else ""
        return _unquote(value) or value or None
    return None


@dataclass
class _Package:
    directory: Path
    files: list[Path] = field(default_factory=list)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _skipped(name: str, extra: set[str]) -> bool:
    return (name in EXCLUDE_DIRS or name in extra or name == "testdata"
            or name.startswith((".", "_")))


def _walk(repo_root: Path, extra: set[str]) -> tuple[dict[Path, str], list[_Package]]:
    """Every go.mod's module path by directory, and every package directory."""
    modules: dict[Path, str] = {}
    packages: list[_Package] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if not _skipped(d, extra))
        directory = Path(dirpath)
        if "go.mod" in filenames:
            text = _read(directory / "go.mod")
            path = module_path(text) if text is not None else None
            if path:
                modules[directory] = path
        files = sorted(directory / f for f in filenames
                       if f.endswith(".go") and not f.endswith("_test.go"))
        if files:
            packages.append(_Package(directory, files))
    return modules, packages


def _import_path(directory: Path, modules: dict[Path, str]) -> str | None:
    """A package directory's import path under its nearest go.mod, or None."""
    for root in (directory, *directory.parents):
        path = modules.get(root)
        if path is not None:
            rel = directory.relative_to(root).as_posix()
            return path if rel == "." else f"{path}/{rel}"
    return None


def _module_id(directory: Path, repo_root: Path) -> str:
    return directory.relative_to(repo_root).as_posix()


class GoBuilder:
    """The Go :class:`lib.import_graph.ImportGraphBuilder`."""

    language = "go"

    def build(
        self, repo_root: Path, extra_exclude_dirs: set[str], scope: Path | None,
    ) -> ModuleGraph | None:
        repo_root = Path(repo_root).resolve()
        modules, packages = _walk(repo_root, extra_exclude_dirs)
        if not modules:
            return None
        by_import: dict[str, str] = {}
        sources: dict[str, list[Path]] = {}
        for package in packages:
            path = _import_path(package.directory, modules)
            if path is None:
                continue
            module = _module_id(package.directory, repo_root)
            by_import[path] = module
            sources[module] = package.files
        if scope is not None:
            scope_abs = scope.resolve()
            sources = {m: files for m, files in sources.items()
                       if (repo_root / m).resolve().is_relative_to(scope_abs)}
        if not sources:
            return None
        edges: dict[str, set[str]] = {}
        surfaces: dict[str, int] = {}
        for module, files in sources.items():
            targets: set[str] = set()
            surface = 0
            for file in files:
                text = _read(file)
                if text is None:
                    continue
                surface += exported_surface(text)
                targets.update(by_import[p] for p in parse_imports(text) if p in by_import)
            targets.discard(module)
            edges[module] = {t for t in targets if t in sources}
            surfaces[module] = surface
        names = sorted(sources)
        return ModuleGraph(
            language=self.language,
            modules=names,
            edges=edges,
            front_doors=set(names),
            module_file={},
            package_of={m: m for m in names},
            surfaces=surfaces,
        )


GO_BUILDER = GoBuilder()
