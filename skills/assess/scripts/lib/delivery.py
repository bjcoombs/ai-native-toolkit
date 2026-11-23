"""Moved to ``lib.hosting.delivery`` (#917). This path is removed in 4.0.0."""
import sys

import lib.hosting.delivery as _moved

sys.modules[__name__] = _moved
