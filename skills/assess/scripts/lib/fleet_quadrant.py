"""Moved to ``lib.fleet.fleet_quadrant`` (#917). This path is removed in 5.0.0."""
import sys

import lib.fleet.fleet_quadrant as _moved

sys.modules[__name__] = _moved
