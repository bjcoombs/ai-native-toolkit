"""Moved to ``lib.run.run_publish`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.run_publish as _moved

sys.modules[__name__] = _moved
