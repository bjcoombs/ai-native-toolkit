"""Moved to ``lib.report.anomaly_detector`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.anomaly_detector as _moved

sys.modules[__name__] = _moved
