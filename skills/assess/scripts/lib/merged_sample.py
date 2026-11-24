"""Moved to ``lib.hosting.merged_sample`` (#917). This path is removed in 5.0.0."""
import sys

import lib.hosting.merged_sample as _moved

sys.modules[__name__] = _moved
