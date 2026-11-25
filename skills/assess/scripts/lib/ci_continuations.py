"""Moved to ``lib.ci.ci_continuations`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.ci_continuations as _moved

sys.modules[__name__] = _moved
