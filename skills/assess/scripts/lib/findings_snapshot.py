"""Moved to ``lib.report.findings_snapshot`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.findings_snapshot as _moved

sys.modules[__name__] = _moved
