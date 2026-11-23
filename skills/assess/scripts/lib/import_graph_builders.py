"""The registered import-graph builders, one per language (issue #708).

``structure_graph.analyze_structure`` and the structure-drift detector run every
builder here through ``lib.graph.import_graph.select_graph``. A new language appends
its builder; the order only decides which failure reason is reported when no
graph is built.

The registered builders (`BUILDERS`), one per language; `structure_graph` and
`structure_drift` both read it. Order decides only which failure is reported when no
graph is built.
"""
from __future__ import annotations

from lib.graph.import_graph import ImportGraphBuilder
from lib.graph.import_graph_go import GO_BUILDER
from lib.graph.import_graph_python import PYTHON_BUILDER
from lib.graph.import_graph_typescript import TYPESCRIPT_BUILDER

BUILDERS: tuple[ImportGraphBuilder, ...] = (PYTHON_BUILDER, GO_BUILDER, TYPESCRIPT_BUILDER)
