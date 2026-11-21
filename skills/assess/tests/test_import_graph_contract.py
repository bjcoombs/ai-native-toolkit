"""The contract every registered import-graph builder keeps (issues #709, #710).

Runs each builder in ``lib.import_graph_builders.BUILDERS`` over one polyglot
tree. A new language adds its files to ``POLYGLOT`` so its builder graphs
something here; the assertions are the shape ``structure_graph`` relies on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.import_graph import LANGUAGE_BY_EXTENSION, ImportGraphBuilder
from lib.import_graph_builders import BUILDERS

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
}


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("polyglot")
    for rel, text in POLYGLOT.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _ids(builder: ImportGraphBuilder) -> str:
    return builder.language


@pytest.mark.parametrize("builder", BUILDERS, ids=_ids)
def test_a_builder_keeps_the_module_graph_contract(
    builder: ImportGraphBuilder, tree: Path,
) -> None:
    assert builder.language in set(LANGUAGE_BY_EXTENSION.values())
    graph = builder.build(tree, set(), None)
    assert graph is not None, f"{builder.language} graphed nothing in the polyglot tree"
    assert graph.language == builder.language
    assert graph.modules and graph.modules == sorted(set(graph.modules))
    modules = set(graph.modules)
    assert set(graph.package_of) == modules
    assert graph.front_doors <= modules
    assert set(graph.module_file) <= modules
    assert set(graph.surfaces) <= modules
    assert set(graph.edges) <= modules
    assert all(path.is_absolute() for path in graph.module_file.values())


@pytest.mark.parametrize("builder", BUILDERS, ids=_ids)
def test_a_builder_returns_none_on_an_empty_tree(
    builder: ImportGraphBuilder, tmp_path: Path,
) -> None:
    assert builder.build(tmp_path, set(), None) is None
