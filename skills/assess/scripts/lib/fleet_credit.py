"""Moved to ``lib.fleet.fleet_credit`` (#917). This path is removed in a later major release."""
import sys

import lib.fleet.fleet_credit as _moved

sys.modules[__name__] = _moved
