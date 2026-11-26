"""Moved to ``lib.history.account_discovery`` (#917). This path is removed in 5.0.0."""
import sys

import lib.history.account_discovery as _moved

sys.modules[__name__] = _moved
