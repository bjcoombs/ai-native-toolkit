"""Moved to ``lib.manifest.check_validation`` (#917). This path is removed in 4.0.0."""
import sys

import lib.manifest.check_validation as _moved

sys.modules[__name__] = _moved
