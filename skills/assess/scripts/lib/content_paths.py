"""Moved to ``lib.history.content_paths`` (#917). This path is removed in 5.0.0."""
import sys

import lib.history.content_paths as _moved

sys.modules[__name__] = _moved
