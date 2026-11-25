"""Moved to ``lib.report.run_wiki`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.run_wiki as _moved

sys.modules[__name__] = _moved
