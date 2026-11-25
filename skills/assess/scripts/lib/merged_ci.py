"""Moved to ``lib.ci.merged_ci`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.merged_ci as _moved

sys.modules[__name__] = _moved
