"""Moved to ``lib.keyhole.python_regions`` (#917). This path is removed in a later major release."""
import sys

import lib.keyhole.python_regions as _moved

sys.modules[__name__] = _moved
