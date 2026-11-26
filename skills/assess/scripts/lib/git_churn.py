"""Moved to ``lib.history.git_churn`` (#917). This path is removed in 5.0.0."""
import sys

import lib.history.git_churn as _moved

sys.modules[__name__] = _moved
