"""Moved to ``lib.ci.ci_unsure`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.ci_unsure as _moved

sys.modules[__name__] = _moved
