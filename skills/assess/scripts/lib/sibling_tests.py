"""Moved to ``lib.testing.sibling_tests`` (#917). This path is removed in 5.0.0."""
import sys

import lib.testing.sibling_tests as _moved

sys.modules[__name__] = _moved
