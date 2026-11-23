"""Moved to ``lib.counts_only.counts_only_rules`` (#917). This path is removed in 4.0.0."""
import sys

import lib.counts_only.counts_only_rules as _moved

sys.modules[__name__] = _moved
