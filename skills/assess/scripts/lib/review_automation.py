"""Moved to ``lib.ci.review_automation`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.review_automation as _moved

sys.modules[__name__] = _moved
