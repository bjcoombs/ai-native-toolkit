"""Moved to ``lib.keyhole.keyhole_signals`` (#917). This path is removed in a later major release."""
import sys

import lib.keyhole.keyhole_signals as _moved

sys.modules[__name__] = _moved
