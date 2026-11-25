"""Moved to ``lib.ci.lint_vocabulary`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.lint_vocabulary as _moved

sys.modules[__name__] = _moved
