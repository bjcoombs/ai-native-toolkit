"""The contract every registered import-graph builder must keep (#708 review, #709).

``structure_graph`` measures any :class:`ModuleGraph` without asking which
language built it, so each builder in ``BUILDERS`` is held to the same shape
here. A new builder adds a fixture tree to ``CONTRACT_FIXTURES``: a registered
builder that graphs none of them fails ``test_every_builder_is_exercised``.

``select_graph`` also guards the one invariant whose breach would crash the
measure (a module with no package): such a graph degrades to a named reason.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.headless_layers import structure
from lib.import_graph import (
    INVALID_IMPORT_GRAPH,
    LANGUAGE_BY_EXTENSION,
    ImportGraphBuilder,
    ModuleGraph,
    select_graph,
)
from lib.import_graph_builders import BUILDERS
from lib.structure_graph import analyze_structure

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_FIXTURES = (
    FIXTURES / "structure_golden" / "repo",  # Python
    FIXTURES / "ts_workspace",               # TypeScript / JavaScript
)


def _graphs(builder: ImportGraphBuilder) -> list[ModuleGraph]:
    built = (builder.build(root.resolve(), set(), None) for root in CONTRACT_FIXTURES)
    return [g for g in built if g is not None and g.modules]


@pytest.mark.parametrize("builder", BUILDERS, ids=lambda b: b.language)
def test_every_builder_is_exercised(builder: ImportGraphBuilder) -> None:
    assert _graphs(builder), f"add a {builder.language} tree to CONTRACT_FIXTURES"


@pytest.mark.parametrize("builder", BUILDERS, ids=lambda b: b.language)
def test_every_builder_keeps_the_module_graph_contract(builder: ImportGraphBuilder) -> None:
    for g in _graphs(builder):
        modules = set(g.modules)
        assert g.modules == sorted(modules), "modules are sorted and unique"
        assert g.language in set(LANGUAGE_BY_EXTENSION.values())
        assert modules <= set(g.package_of), "package_of covers every module"
        assert set(g.edges) <= modules, "every importer is a module"
        assert g.front_doors <= modules
        assert set(g.module_file) <= modules
        assert all(p.is_absolute() and p.is_file() for p in g.module_file.values())
        assert set(g.surfaces) <= modules
        assert all(isinstance(n, int) and n >= 0 for n in g.surfaces.values())


@pytest.mark.parametrize("builder", BUILDERS, ids=lambda b: b.language)
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
