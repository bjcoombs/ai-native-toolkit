"""Moved to ``lib.scoring.levels_hints`` (#917). This path is removed in 5.0.0."""
import sys

import lib.scoring.levels_hints as _moved

sys.modules[__name__] = _moved
