"""Moved to ``lib.run.context_blocks`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.context_blocks as _moved

sys.modules[__name__] = _moved
