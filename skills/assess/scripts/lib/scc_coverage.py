"""Moved to ``lib.complexity.scc_coverage`` (#917). This path is removed in 4.0.0."""
import sys

import lib.complexity.scc_coverage as _moved

sys.modules[__name__] = _moved
