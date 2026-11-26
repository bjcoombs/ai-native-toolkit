"""Moved to ``lib.run.headless_static`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.headless_static as _moved

sys.modules[__name__] = _moved
