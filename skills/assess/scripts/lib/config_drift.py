"""Moved to ``lib.hosting.config_drift`` (#917). This path is removed in a later major release."""
import sys

import lib.hosting.config_drift as _moved

sys.modules[__name__] = _moved
