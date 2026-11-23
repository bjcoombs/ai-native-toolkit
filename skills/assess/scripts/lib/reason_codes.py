"""Moved to ``lib.counts_only.reason_codes`` (#917). This path is removed in 4.0.0."""
import sys

import lib.counts_only.reason_codes as _moved

sys.modules[__name__] = _moved
