"""Moved to ``lib.docs.ownership_parser`` (#917). This path is removed in 4.0.0."""
import sys

import lib.docs.ownership_parser as _moved

sys.modules[__name__] = _moved
