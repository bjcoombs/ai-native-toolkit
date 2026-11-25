"""Moved to ``lib.scoring.signal_grades`` (#917). This path is removed in 5.0.0."""
import sys

import lib.scoring.signal_grades as _moved

sys.modules[__name__] = _moved
