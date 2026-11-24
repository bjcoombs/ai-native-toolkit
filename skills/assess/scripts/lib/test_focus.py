"""Moved to ``lib.testing.test_focus`` (#917). This path is removed in 5.0.0."""
import sys

import lib.testing.test_focus as _moved

sys.modules[__name__] = _moved
