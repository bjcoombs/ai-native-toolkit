"""Moved to ``lib.run.degrade`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.degrade as _moved

sys.modules[__name__] = _moved
