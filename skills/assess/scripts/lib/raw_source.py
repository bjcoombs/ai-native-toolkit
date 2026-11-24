"""Moved to ``lib.complexity.raw_source`` (#917). This path is removed in a later major release."""
import sys

import lib.complexity.raw_source as _moved

sys.modules[__name__] = _moved
