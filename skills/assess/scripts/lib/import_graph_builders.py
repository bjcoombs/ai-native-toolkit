"""The registered import-graph builders, one per language (issue #708).

``structure_graph.analyze_structure`` and the structure-drift detector run every
builder here through ``lib.import_graph.select_graph``. A new language appends
its builder; the order only decides which failure reason is reported when no
graph is built.
"""
from __future__ import annotations

from lib.import_graph import ImportGraphBuilder
from lib.import_graph_python import PYTHON_BUILDER

BUILDERS: tuple[ImportGraphBuilder, ...] = (PYTHON_BUILDER,)
