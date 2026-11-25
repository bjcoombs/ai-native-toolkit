"""Moved to ``lib.ci.ci_statements`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.ci_statements as _moved

sys.modules[__name__] = _moved
