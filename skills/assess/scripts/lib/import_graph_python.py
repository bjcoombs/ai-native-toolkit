"""Moved to ``lib.graph.import_graph_python`` (#917). This path is removed in a later major release."""
import sys

import lib.graph.import_graph_python as _moved

sys.modules[__name__] = _moved
