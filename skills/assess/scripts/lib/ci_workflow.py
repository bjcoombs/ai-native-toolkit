"""Moved to ``lib.report.ci_workflow`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.ci_workflow as _moved

sys.modules[__name__] = _moved
