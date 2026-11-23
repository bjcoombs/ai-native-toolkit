"""Moved to ``lib.testing.coverage_root_config`` (#917). This path is removed in 4.0.0."""
import sys

import lib.testing.coverage_root_config as _moved

sys.modules[__name__] = _moved
