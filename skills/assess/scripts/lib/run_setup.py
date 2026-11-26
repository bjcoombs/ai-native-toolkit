"""Moved to ``lib.run.run_setup`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.run_setup as _moved

sys.modules[__name__] = _moved
