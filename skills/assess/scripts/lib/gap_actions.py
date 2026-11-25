"""Moved to ``lib.report.gap_actions`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.gap_actions as _moved

sys.modules[__name__] = _moved
