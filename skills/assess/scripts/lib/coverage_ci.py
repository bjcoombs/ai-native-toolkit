"""Moved to ``lib.testing.coverage_ci`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.coverage_ci as _moved

sys.modules[__name__] = _moved
