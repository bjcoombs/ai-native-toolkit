"""Moved to ``lib.history.commit_anchor`` (#917). This path is removed in 5.0.0."""
import sys

import lib.history.commit_anchor as _moved

sys.modules[__name__] = _moved
