"""Moved to ``lib.report.badge`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.badge as _moved

sys.modules[__name__] = _moved
