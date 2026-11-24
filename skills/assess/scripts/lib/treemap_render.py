"""Moved to ``lib.complexity.treemap_render`` (#917). This path is removed in a later major release."""
import sys

import lib.complexity.treemap_render as _moved

sys.modules[__name__] = _moved
