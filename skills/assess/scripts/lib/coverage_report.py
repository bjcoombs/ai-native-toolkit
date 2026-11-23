"""Moved to ``lib.testing.coverage_report`` (#917). This path is removed in 4.0.0."""
import sys

import lib.testing.coverage_report as _moved

sys.modules[__name__] = _moved
