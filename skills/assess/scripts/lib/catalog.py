"""Moved to ``lib.report.catalog`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.catalog as _moved

sys.modules[__name__] = _moved
