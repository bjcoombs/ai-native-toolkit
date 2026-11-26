"""Moved to ``lib.history.history_window`` (#917). This path is removed in 5.0.0."""
import sys

import lib.history.history_window as _moved

sys.modules[__name__] = _moved
