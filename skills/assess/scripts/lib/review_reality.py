"""Moved to ``lib.hosting.review_reality`` (#917). This path is removed in 5.0.0."""
import sys

import lib.hosting.review_reality as _moved

sys.modules[__name__] = _moved
