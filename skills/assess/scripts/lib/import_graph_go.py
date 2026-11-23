"""Moved to ``lib.graph.import_graph_go`` (#917). This path is removed in 4.0.0."""
import sys

import lib.graph.import_graph_go as _moved

sys.modules[__name__] = _moved
