"""Moved to ``lib.report.catalog_tags`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.catalog_tags as _moved

sys.modules[__name__] = _moved
