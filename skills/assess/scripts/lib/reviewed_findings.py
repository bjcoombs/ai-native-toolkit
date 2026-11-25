"""Moved to ``lib.report.reviewed_findings`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.reviewed_findings as _moved

sys.modules[__name__] = _moved
