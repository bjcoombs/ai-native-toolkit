"""Moved to ``lib.history.fleet_activity`` (#917). This path is removed in 5.0.0."""
import sys

import lib.history.fleet_activity as _moved

sys.modules[__name__] = _moved
