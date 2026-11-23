"""Moved to ``lib.testing.coverage_activation`` (#917). This path is removed in 4.0.0."""
import sys

import lib.testing.coverage_activation as _moved

sys.modules[__name__] = _moved
