"""Language-neutral import-graph builder interface (issues #707, #708).

``structure_graph`` measures A1-A4 over one module graph and never asks which
language produced it. A builder turns a repository's source in one language into
a :class:`ModuleGraph`; :func:`select_graph` runs every registered builder
and returns the dominant source language's graph (or its failure), falling back
to the largest other graph only when no builder covers that language. A repository with no graph
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

The language-neutral builder interface (issues #708, #709). A builder is any object with a
`language` token and `build(repo_root, extra_exclude_dirs, scope) -> ModuleGraph | None`
(the `ImportGraphBuilder` protocol). `build` returns `None` when the repository holds
nothing in its language, and raises `GraphUnavailable(reason)` when its tool is missing
or fails; the reason sentence then reaches the block. A `ModuleGraph` carries:

| Field | Meaning |
|-------|---------|
| `language` | the token the block reports and the `no_import_graph_<language>` suffix |
| `modules` | sorted module ids; module ids are paths or path-like names, hashed by the counts-only PATH rule |
| `edges` | importer -> the modules it imports directly; a target outside `modules` is ignored |
| `front_doors` | the modules a package exposes as its public API (A3) |
| `module_file` | module -> absolute source file; a module without one reads size 0 |
| `package_of` | module -> package boundary id (A3 contract edges, A4 per-package splits) |
| `surfaces` | module -> public definitions it exposes (A1); leave empty when the language has no measure, and every module reads 0 |
| `name_collisions` | packages sharing a name, disclosed rather than merged |

`select_graph(repo_root, builders, extra_exclude_dirs, scope)` runs every builder. The
dominant source language decides: when a builder covers it (its `language`, or a token in
an optional `languages` attribute; the TypeScript builder covers `javascript` too), that
builder's graph is returned and its failure is final (the count honours a builder's
optional `is_graph_source(rel)`, so files it skips, such as a Python app's
`static/js/*.min.js`, never make their language dominant). A failure, a missing tool or an
invalid graph, keeps its reason, so a missing grimp on a Python repository still reads
`no_grimp` rather than yielding to a stray `.mjs` file. Graph sizes are never compared with
the dominant language's graph, whose nodes can differ. When the dominant builder found
nothing to graph (loose scripts, no package) or no builder covers the dominant language,
the largest other graph stands in (most modules, then most internal edges, then the
language token); with none the reason is the first builder failure in registration order, else
`no_graph_reason`, which counts source files per language
(`LANGUAGE_BY_EXTENSION`, skipping `EXCLUDE_DIRS` and the configured excludes, within
each `scope` root; `scope` is a `lib.run.run_scope.Scope`, #1110) and names the dominant one. A built graph that breaks the contract (a module
missing from `package_of`, which A3 and A4 index) is treated as a builder failure
(`graph_defect`, reason `invalid_import_graph: ...`), so another language can still be
reported and the measure never raises `KeyError`. `is_graph_source(rel_path, language)`
says whether a file could be in that language's graph: its extension must be one of the
languages the covering builder graphs (the TypeScript builder covers `.js` too), and the
builder's own optional `is_graph_source(rel)` hook must accept it (Go skips test files and
`GO_SKIP_DIRS`; TypeScript skips bundles, declarations, tests and tool configs). Imports
no graph tool; the registered builders are loaded lazily when none are passed.

Worked example: a builder for a language whose package is a directory and whose front
door is the directory's `index` file.

```python
from pathlib import Path

from lib.graph.import_graph import GraphUnavailable, ModuleGraph
from lib.run.run_scope import Scope, scope_roots


class IndexDirBuilder:
    language = "typescript"

    def build(self, repo_root: Path, extra_exclude_dirs: set[str],
              scope: Scope | None) -> ModuleGraph | None:
        files = sorted(p for root in scope_roots(scope, repo_root) for p in root.rglob("*.ts")
                       if not set(p.relative_to(repo_root).parts) & extra_exclude_dirs)
        if not files:
            return None                        # nothing to graph: the dominant-language reason applies
        try:
            imports = read_imports(files)      # your scan: module id -> module ids it imports
        except OSError as e:
            raise GraphUnavailable(f"typescript import scan failed ({e})") from e
        ids = {p: p.relative_to(repo_root).with_suffix("").as_posix() for p in files}
        return ModuleGraph(
            language=self.language,
            modules=sorted(ids.values()),
            edges=imports,
            front_doors={m for m in ids.values() if m.rsplit("/", 1)[-1] == "index"},
            module_file={m: p for p, m in ids.items()},
            package_of={m: m.rsplit("/", 1)[0] if "/" in m else "." for m in ids.values()},
        )
```

Register it by appending to `BUILDERS` in `import_graph_builders.py` and add files in
its language to `POLYGLOT` in `tests/test_import_graph_contract.py`, which checks every
registered builder's graph shape (`package_of` covers `modules`). A new block field
or reason code still needs its counts-only rule (`counts_only_rules.py`) and a note in
the maturity model header (`levels/default.toml`).
Tests: `tests/test_import_graph.py`, and `tests/test_import_graph_contract.py`, the
contract every registered builder must keep (sorted unique modules, `package_of`
covering them, front doors, files and surfaces within them, `None` on an empty tree).
A new builder adds a fixture tree to its `CONTRACT_FIXTURES`.
"""
from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypedDict

from lib.run.run_scope import Scope, scope_roots
from lib.counts_only.counts_only_rules import REASON_SENTENCE_CODES

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

# Every code a ``structure.reason`` can open with or map to: the allow-list a
# counts-only run keeps (#764). Derived from the constants above, so a new
# language or code cannot drift from it; anything else reads ``unavailable``.
STRUCTURE_REASON_CODES: frozenset[str] = frozenset({
    NO_SOURCE_FILES.split(":", 1)[0],
    INVALID_IMPORT_GRAPH,
    *(f"no_import_graph_{lang}" for lang in LANGUAGE_BY_EXTENSION.values()),
    *(code for _, code in REASON_SENTENCE_CODES),
})


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
        self, repo_root: Path, extra_exclude_dirs: set[str], scope: Scope | None,
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
    scope: Scope | None = None,
) -> GraphSelection:
    """Build one graph per language; the dominant language's graph wins.

    The dominant language (most graphable source files, :func:`dominant_language`)
    decides first. When a registered builder covers it (its ``language``, or any
    token in an optional ``languages`` attribute), its graph is returned, and its
    failure is final: a missing tool or an invalid graph keeps its reason (and
    install hint) rather than yielding to a minor language's graph. Graph sizes
    are never compared with the dominant language's graph, whose nodes can differ
    (files in one, packages in another).

    When the dominant builder found nothing to graph (loose scripts with no
    package), or no builder covers the dominant language, another language
    stands in: the largest graph (most modules, then edges, then the language
    token), else the first failure in registration order, else
    :func:`no_graph_reason`. A graph that breaks the contract
    (:func:`graph_defect`) counts as its builder's failure.
    """
    repo_root = Path(repo_root).resolve()
    extra = extra_exclude_dirs or set()
    dominant = dominant_language(repo_root, extra, scope, builders)
    graphs: list[ModuleGraph] = []
    failures: list[str] = []
    for builder in builders:
        covers = dominant is not None and dominant in builder_languages(builder)
        try:
            graph = builder.build(repo_root, extra, scope)
            defect = graph_defect(graph) if graph is not None and graph.modules else None
            if defect is not None:
                raise GraphUnavailable(defect)
        except GraphUnavailable as e:
            if covers:
                return GraphSelection(graph=None, reason=str(e))
            failures.append(str(e))
            continue
        if covers and graph is not None and graph.modules:
            return GraphSelection(graph=graph)
        if graph is not None and graph.modules:
            graphs.append(graph)
    if graphs:
        best = min(graphs, key=lambda g: (-_size(g)[0], -_size(g)[1], g.language))
        return GraphSelection(graph=best)
    if failures:
        return GraphSelection(graph=None, reason=failures[0])
    return GraphSelection(graph=None, reason=no_graph_reason(repo_root, extra, scope, builders))


def builder_languages(builder: ImportGraphBuilder) -> set[str]:
    """The language tokens a builder covers: ``language`` plus any ``languages``."""
    extra = getattr(builder, "languages", ())
    return {builder.language, *(t for t in extra if isinstance(t, str))}


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
    repo_root: Path, extra_exclude_dirs: set[str] | None = None, scope: Scope | None = None,
    builders: Sequence[ImportGraphBuilder] = (),
) -> Counter[str]:
    """Source files per language under each ``scope`` root (default ``repo_root``).

    A builder in ``builders`` covering a language may define
    ``is_graph_source(rel: Path) -> bool`` (``rel`` relative to the walk's
    start); a file of that language then counts only when it says True, so files
    the builder skips (minified bundles, declarations, tests, tool configs) do
    not make their language dominant.
    """
    extra = extra_exclude_dirs or set()
    filters: dict[str, Callable[[Path], bool]] = {}
    for builder in builders:
        check = getattr(builder, "is_graph_source", None)
        if callable(check):
            for token in builder_languages(builder):
                filters.setdefault(token, check)
    counts: Counter[str] = Counter()
    for start, (dirpath, dirnames, filenames) in ((s, w) for s in scope_roots(scope, repo_root)
                                                  for w in os.walk(s)):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS and d not in extra)
        for name in filenames:
            language = LANGUAGE_BY_EXTENSION.get(os.path.splitext(name)[1].lower())
            if language is None:
                continue
            check = filters.get(language)
            if check is not None and not check(Path(dirpath, name).relative_to(start)):
                continue
            counts[language] += 1
    return counts


def dominant_language(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None, scope: Scope | None = None,
    builders: Sequence[ImportGraphBuilder] = (),
) -> str | None:
    """The language with the most source files (ties by name), or None.

    ``builders`` narrows each covered language to the files its builder graphs
    (:func:`language_counts`).
    """
    counts = language_counts(repo_root, extra_exclude_dirs, scope, builders)
    if not counts:
        return None
    return min(counts, key=lambda lang: (-counts[lang], lang))


def no_graph_reason(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None, scope: Scope | None = None,
    builders: Sequence[ImportGraphBuilder] = (),
) -> str:
    """Why no import graph was built, naming the dominant source language."""
    language = dominant_language(repo_root, extra_exclude_dirs, scope, builders)
    if language is None:
        return NO_SOURCE_FILES
    if language == "python":
        return NO_PYTHON_PACKAGES
    return (f"no_import_graph_{language}: no import graph was built for {language}, "
            "the repository's dominant source language")


def is_graph_source(
    rel_path: Path, language: str, builders: Sequence[ImportGraphBuilder] | None = None,
) -> bool:
    """True when a repo-relative file could be in ``language``'s graph.

    The builder covering ``language`` (``builders``, default the registered
    ones) decides: the file's extension must map to a language it covers (the
    TypeScript builder graphs ``.js`` too), and its optional
    ``is_graph_source(rel)`` hook must accept the file (Go skips ``_test.go``
    and ``GO_SKIP_DIRS``; TypeScript skips bundles, declarations, tests and tool
    configs). A language no builder covers keeps the extension check alone.
    """
    if builders is None:
        from lib.import_graph_builders import BUILDERS  # lazy: builders import this module
        builders = BUILDERS
    covering = next((b for b in builders if language in builder_languages(b)), None)
    covered = builder_languages(covering) if covering is not None else {language}
    if LANGUAGE_BY_EXTENSION.get(rel_path.suffix.lower()) not in covered:
        return False
    check = getattr(covering, "is_graph_source", None)
    return bool(check(rel_path)) if callable(check) else True
