"""Moved to ``lib.fleet.fleet_doc_freshness`` (#917). This path is removed in 4.0.0."""
import sys

import lib.fleet.fleet_doc_freshness as _moved

sys.modules[__name__] = _moved
