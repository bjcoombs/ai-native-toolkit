"""Moved to ``lib.ci.ci_merged_view`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.ci_merged_view as _moved

sys.modules[__name__] = _moved
