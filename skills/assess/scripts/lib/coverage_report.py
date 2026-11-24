"""Moved to ``lib.testing.coverage_report`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.coverage_report as _moved

sys.modules[__name__] = _moved
