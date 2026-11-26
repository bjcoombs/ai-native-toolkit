"""The contract every registered import-graph builder keeps (issues #708, #709, #710).

``structure_graph`` measures any :class:`ModuleGraph` without asking which
language built it, so each builder in ``BUILDERS`` is held to the same shape
here, over one polyglot tree (``POLYGLOT``) and the fixture trees in
``CONTRACT_FIXTURES``. A new language adds its files to ``POLYGLOT``: a
registered builder that graphs nothing there fails.

``select_graph`` also guards the one invariant whose breach would crash the
measure (a module with no package): such a graph degrades to a named reason.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.run.headless_layers import structure
from lib.graph.import_graph import (
    INVALID_IMPORT_GRAPH,
    LANGUAGE_BY_EXTENSION,
    ImportGraphBuilder,
    ModuleGraph,
    builder_languages,
    select_graph,
)
from lib.import_graph_builders import BUILDERS
from lib.graph.structure_graph import analyze_structure

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_FIXTURES = (
    FIXTURES / "structure_golden" / "repo",  # Python
    FIXTURES / "ts_workspace",               # TypeScript / JavaScript
)

POLYGLOT: dict[str, str] = {
    # Python
    "pyapp/__init__.py": "",
    "pyapp/core.py": "def run():\n    return 1\n",
    "pyapp/api.py": "from pyapp import core\n",
    "pylib/__init__.py": "from pyapp.core import run\n",
    # Go
    "go.mod": "module example.com/org/poly\n",
    "gosvc/svc.go": 'package gosvc\nimport "example.com/org/poly/gosvc/util"\n',
    "gosvc/util/util.go": "package util\nfunc Help() {}\n",
    # TypeScript
    "web/index.ts": "export { help } from './util';\n",
    "web/util.ts": "export function help(): number { return 1; }\n",
}


def _ids(builder: ImportGraphBuilder) -> str:
    return builder.language


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("polyglot")
    for rel, text in POLYGLOT.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _graphs(builder: ImportGraphBuilder, tree: Path) -> list[ModuleGraph]:
    roots = (tree, *CONTRACT_FIXTURES)
    built = (builder.build(root.resolve(), set(), None) for root in roots)
    return [g for g in built if g is not None and g.modules]


@pytest.mark.parametrize("builder", BUILDERS, ids=_ids)
def test_every_builder_graphs_the_polyglot_tree(
    builder: ImportGraphBuilder, tree: Path,
) -> None:
    graph = builder.build(tree, set(), None)
    assert graph is not None and graph.modules, (
        f"{builder.language} graphed nothing: add its files to POLYGLOT")


@pytest.mark.parametrize("builder", BUILDERS, ids=_ids)
def test_every_builder_keeps_the_module_graph_contract(
    builder: ImportGraphBuilder, tree: Path,
) -> None:
    assert builder.language in set(LANGUAGE_BY_EXTENSION.values())
    for g in _graphs(builder, tree):
        modules = set(g.modules)
        assert g.language in builder_languages(builder)
        assert g.modules == sorted(modules), "modules are sorted and unique"
        assert set(g.package_of) == modules, "package_of covers exactly the modules"
        assert set(g.edges) <= modules, "every importer is a module"
        assert g.front_doors <= modules
        assert set(g.module_file) <= modules
        assert all(p.is_absolute() and p.is_file() for p in g.module_file.values())
        assert set(g.surfaces) <= modules
        assert all(isinstance(n, int) and n >= 0 for n in g.surfaces.values())


@pytest.mark.parametrize("builder", BUILDERS, ids=_ids)
def test_every_builder_returns_none_on_an_empty_tree(
    builder: ImportGraphBuilder, tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    assert builder.build(tmp_path, set(), None) is None


class _NoPackageBuilder:
    """A builder whose graph leaves a module without a package."""

    def __init__(self, language: str = "go") -> None:
        self.language = language

    def build(self, repo_root: Path, extra_exclude_dirs: set[str],
              scope: Path | None) -> ModuleGraph | None:
        return ModuleGraph(
            language=self.language, modules=["a/x", "b/y"], edges={"a/x": {"b/y"}},
            front_doors=set(), module_file={}, package_of={"a/x": "a"},
        )


def test_a_graph_missing_a_package_degrades_with_a_named_reason(tmp_path: Path) -> None:
    block = analyze_structure(tmp_path, builders=(_NoPackageBuilder(),)).as_dict()
    assert block["available"] is False
    assert block["reason"].startswith(f"{INVALID_IMPORT_GRAPH}: the go builder ")
    assert structure(block)["reason"].startswith(f"{INVALID_IMPORT_GRAPH}: ")


def test_a_broken_graph_does_not_sink_a_sound_one() -> None:
    root = (FIXTURES / "ts_workspace").resolve()
    builders = (_NoPackageBuilder("rust"), *BUILDERS)
    selection = select_graph(root, builders)
    assert selection.graph is not None and selection.graph.language == "typescript"
