"""Moved to ``lib.scoring.next_step_guidance`` (#917). This path is removed in 5.0.0."""
import sys

import lib.scoring.next_step_guidance as _moved

sys.modules[__name__] = _moved
