"""Moved to ``lib.report.gate_cost`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.gate_cost as _moved

sys.modules[__name__] = _moved
