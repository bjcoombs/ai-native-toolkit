"""Moved to ``lib.counts_only.counts_only_schema`` (#917). This path is removed in 5.0.0."""
import sys

import lib.counts_only.counts_only_schema as _moved

sys.modules[__name__] = _moved
