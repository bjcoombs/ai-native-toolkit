"""Moved to ``lib.testing.test_focus`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.test_focus as _moved

sys.modules[__name__] = _moved
