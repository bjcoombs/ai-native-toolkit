"""Moved to ``lib.keyhole.python_regions`` (#917). This path is removed in 5.0.0."""
import sys

import lib.keyhole.python_regions as _moved

sys.modules[__name__] = _moved
