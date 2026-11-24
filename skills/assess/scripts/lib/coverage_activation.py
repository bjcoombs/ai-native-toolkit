"""Moved to ``lib.testing.coverage_activation`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.coverage_activation as _moved

sys.modules[__name__] = _moved
