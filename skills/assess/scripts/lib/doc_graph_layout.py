"""Moved to ``lib.docs.doc_graph_layout`` (#917). This path is removed in 4.0.0."""
import sys

import lib.docs.doc_graph_layout as _moved

sys.modules[__name__] = _moved
