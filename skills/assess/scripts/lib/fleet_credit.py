"""Moved to ``lib.fleet.fleet_credit`` (#917). This path is removed in 4.0.0."""
import sys

import lib.fleet.fleet_credit as _moved

sys.modules[__name__] = _moved
