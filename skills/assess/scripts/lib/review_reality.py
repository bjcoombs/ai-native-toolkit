"""Moved to ``lib.hosting.review_reality`` (#917). This path is removed in a later major release."""
import sys

import lib.hosting.review_reality as _moved

sys.modules[__name__] = _moved
