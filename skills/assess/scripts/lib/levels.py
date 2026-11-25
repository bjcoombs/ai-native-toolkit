"""Moved to ``lib.scoring.levels`` (#917). This path is removed in 5.0.0."""
import sys

import lib.scoring.levels as _moved

sys.modules[__name__] = _moved
