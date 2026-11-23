"""Moved to ``lib.fleet.fleet_tooling_map`` (#917). This path is removed in 4.0.0."""
import sys

import lib.fleet.fleet_tooling_map as _moved

sys.modules[__name__] = _moved
