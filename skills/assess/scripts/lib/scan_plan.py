"""Moved to ``lib.run.scan_plan`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.scan_plan as _moved

sys.modules[__name__] = _moved
