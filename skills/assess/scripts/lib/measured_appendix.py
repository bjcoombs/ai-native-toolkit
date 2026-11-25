"""Moved to ``lib.report.measured_appendix`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.measured_appendix as _moved

sys.modules[__name__] = _moved
