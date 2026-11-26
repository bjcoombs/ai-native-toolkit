"""Moved to ``lib.run.run_scope`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.run_scope as _moved

sys.modules[__name__] = _moved
