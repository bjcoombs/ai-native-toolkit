"""Moved to ``lib.fleet.fleet_slope`` (#917). This path is removed in 4.0.0."""
import sys

import lib.fleet.fleet_slope as _moved

sys.modules[__name__] = _moved
