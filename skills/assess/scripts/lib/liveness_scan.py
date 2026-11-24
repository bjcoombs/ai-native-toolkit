"""Moved to ``lib.keyhole.liveness_scan`` (#917). This path is removed in a later major release."""
import sys

import lib.keyhole.liveness_scan as _moved

sys.modules[__name__] = _moved
