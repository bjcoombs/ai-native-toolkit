"""Moved to ``lib.complexity.stats_diff`` (#917). This path is removed in 4.0.0."""
import sys

import lib.complexity.stats_diff as _moved

sys.modules[__name__] = _moved
