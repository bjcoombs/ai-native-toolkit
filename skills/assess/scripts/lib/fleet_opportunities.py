"""Moved to ``lib.fleet.fleet_opportunities`` (#917). This path is removed in a later major release."""
import sys

import lib.fleet.fleet_opportunities as _moved

sys.modules[__name__] = _moved
