"""Moved to ``lib.ci.ci_compound`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.ci_compound as _moved

sys.modules[__name__] = _moved
