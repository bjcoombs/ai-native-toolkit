"""Moved to ``lib.complexity.diff_reliability`` (#917). This path is removed in a later major release."""
import sys

import lib.complexity.diff_reliability as _moved

sys.modules[__name__] = _moved
