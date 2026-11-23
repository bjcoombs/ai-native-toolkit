"""Moved to ``lib.keyhole.liveness_scan`` (#917). This path is removed in 4.0.0."""
import sys

import lib.keyhole.liveness_scan as _moved

sys.modules[__name__] = _moved
