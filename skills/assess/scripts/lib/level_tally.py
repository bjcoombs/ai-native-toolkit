"""Moved to ``lib.scoring.level_tally`` (#917). This path is removed in 5.0.0."""
import sys

import lib.scoring.level_tally as _moved

sys.modules[__name__] = _moved
