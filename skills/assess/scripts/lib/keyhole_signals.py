"""Moved to ``lib.keyhole.keyhole_signals`` (#917). This path is removed in 5.0.0."""
import sys

import lib.keyhole.keyhole_signals as _moved

sys.modules[__name__] = _moved
