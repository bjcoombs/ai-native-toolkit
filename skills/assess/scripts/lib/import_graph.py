"""Language-neutral import-graph builder interface (issues #707, #708).

``structure_graph`` measures A1-A4 over one module graph and never asks which
language produced it. A builder turns a repository's source in one language into
a :class:`ModuleGraph`; :func:`select_graph` runs every registered builder,
keeps one graph per language and returns the largest. A repository with no graph
gets a reason that names its dominant source language
(``no_import_graph_rust``), so a fleet reader can tell a tooling gap from a
repository with no code (``no_source_files``).

A builder is any object with a ``language`` token and a ``build`` method (the
:class:`ImportGraphBuilder` protocol). ``build`` returns ``None`` when the
repository holds nothing it can graph, and raises :class:`GraphUnavailable` with a
reason sentence when its tool is missing or fails, so the reason survives in the
block. The registered builders live in ``lib.import_graph_builders``.

This module imports no graph tool; it is types, dispatch and the dominant-language
count only.
"""
from __future__ import annotations

import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypedDict

# Directories never worth walking for source. Mirrors doc_graph's EXCLUDE_DIRS
# (kept local so this module pulls in no heavy deps): build artefacts, vendor
# trees, virtualenvs and /assess's own output are not the repo's source.
EXCLUDE_DIRS = frozenset({
    ".git", "node_modules", "dist", "build", "target", "vendor",
    ".venv", "venv", "__pycache__", ".gradle", ".idea", ".mvn",
    "worktree", ".understand-anything", ".obsidian", ".taskmaster",
    ".claude", ".next", ".nuxt", ".output", ".svelte-kit", ".astro",
    "out", "coverage", "htmlcov", "Pods", "DerivedData", "flutter_assets",
    ".assess", "mutants", "tests", "test",
})

# Source extension -> language token. The token is the reason-code suffix, so it
# stays lower-case ASCII with no separator. Shell counts: a shell-heavy repository
# reads no_import_graph_shell, which tells a fleet reader what the code is rather
# than that there is none. ``.h`` does not: C, C++ and Objective-C share it, so a
# header-heavy C++ repository would read as C.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala",
    ".groovy": "groovy",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".dart": "dart",
    ".c": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".clj": "clojure",
    ".lua": "lua",
    ".pl": "perl",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
}

# Reason when the dominant language is Python and no importable package exists;
# the headless runner maps it to ``no_python_packages``.
NO_PYTHON_PACKAGES = "no importable Python packages found"
INVALID_IMPORT_GRAPH = "invalid_import_graph"
NO_SOURCE_FILES = "no_source_files: no source file in a language /assess recognises"


class NameCollision(TypedDict):
    """Two or more discovered packages sharing one top-level import name."""

    name: str
    paths: list[str]


@dataclass
class ModuleGraph:
    """One language's import graph, in the terms ``analyze_structure`` measures.

    ``modules`` is sorted. ``edges`` maps an importer to the modules it imports
    directly; a target outside ``modules`` (an external import) is ignored.
    ``front_doors`` are the modules a package exposes as its public API (A3).
    ``module_file`` maps a module to its source file (absolute path); a module
    with no file reads size 0. ``package_of`` maps each module to its package
    boundary id: an edge between two packages is a contract edge (A3), and each
    package is judged for a split on its own (A4). ``surfaces`` holds the public
    definitions a module exposes (A1); a language that does not measure them
    leaves it empty and every module reads 0.
    """

    language: str
    modules: list[str]
    edges: dict[str, set[str]]
    front_doors: set[str]
    module_file: dict[str, Path]
    package_of: dict[str, str]
    surfaces: dict[str, int] = field(default_factory=dict)
    name_collisions: list[NameCollision] = field(default_factory=list)

    @property
    def packages(self) -> list[str]:
        """The package boundary ids, sorted."""
        return sorted(set(self.package_of.values()))


class GraphUnavailable(Exception):
    """A builder's tool is missing or failed; ``str(exc)`` is the reason sentence."""


class ImportGraphBuilder(Protocol):
    """Builds one language's :class:`ModuleGraph` from a repository."""

    @property
    def language(self) -> str: ...

    def build(
        self, repo_root: Path, extra_exclude_dirs: set[str], scope: Path | None,
    ) -> ModuleGraph | None: ...


@dataclass
class GraphSelection:
    """The graph to report (the largest built), or the reason there is none."""

    graph: ModuleGraph | None
    reason: str = ""


def _size(graph: ModuleGraph) -> tuple[int, int]:
    """(modules, internal edges): the order graphs are compared in."""
    known = set(graph.modules)
    return len(known), sum(len(t & known) for t in graph.edges.values())


def select_graph(
    repo_root: Path,
    builders: Sequence[ImportGraphBuilder],
    extra_exclude_dirs: set[str] | None = None,
    scope: Path | None = None,
) -> GraphSelection:
    """Build one graph per language and keep the largest.

    Largest is the most modules, then the most edges, then the language token
    for a stable tie-break. With no graph built, the reason is the first
    builder failure (in registration order) or, when none failed, the
    dominant-language reason from :func:`no_graph_reason`.
    """
    repo_root = Path(repo_root).resolve()
    extra = extra_exclude_dirs or set()
    graphs: list[ModuleGraph] = []
    failures: list[str] = []
    for builder in builders:
        try:
            graph = builder.build(repo_root, extra, scope)
        except GraphUnavailable as e:
            failures.append(str(e))
            continue
        if graph is not None and graph.modules:
            defect = graph_defect(graph)
            if defect is not None:
                failures.append(defect)
                continue
            graphs.append(graph)
    if graphs:
        best = min(graphs, key=lambda g: (-_size(g)[0], -_size(g)[1], g.language))
        return GraphSelection(graph=best)
    if failures:
        return GraphSelection(graph=None, reason=failures[0])
    return GraphSelection(graph=None, reason=no_graph_reason(repo_root, extra, scope))


def graph_defect(graph: ModuleGraph) -> str | None:
    """Why ``graph`` breaks the :class:`ModuleGraph` contract, or None.

    A3 and A4 index ``package_of`` by every module, so a module with no package
    would raise ``KeyError`` mid-measure. ``select_graph`` treats such a graph as a
    builder failure instead: another language can still be reported, and with
    none the block degrades with this reason.
    """
    missing = [m for m in graph.modules if m not in graph.package_of]
    if missing:
        return (f"{INVALID_IMPORT_GRAPH}: the {graph.language} builder returned "
                f"{len(missing)} module(s) with no package; structure not assessed")
    return None


def language_counts(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None, scope: Path | None = None,
) -> Counter[str]:
    """Source files per language under ``scope`` (default ``repo_root``)."""
    extra = extra_exclude_dirs or set()
    start = scope.resolve() if scope is not None else Path(repo_root).resolve()
    counts: Counter[str] = Counter()
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS and d not in extra)
        for name in filenames:
            language = LANGUAGE_BY_EXTENSION.get(os.path.splitext(name)[1].lower())
            if language is not None:
                counts[language] += 1
    return counts


def dominant_language(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None, scope: Path | None = None,
) -> str | None:
    """The language with the most source files (ties by name), or None."""
    counts = language_counts(repo_root, extra_exclude_dirs, scope)
    if not counts:
        return None
    return min(counts, key=lambda lang: (-counts[lang], lang))


def no_graph_reason(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None, scope: Path | None = None,
) -> str:
    """Why no import graph was built, naming the dominant source language."""
    language = dominant_language(repo_root, extra_exclude_dirs, scope)
    if language is None:
        return NO_SOURCE_FILES
    if language == "python":
        return NO_PYTHON_PACKAGES
    return (f"no_import_graph_{language}: no import graph was built for {language}, "
            "the repository's dominant source language")
