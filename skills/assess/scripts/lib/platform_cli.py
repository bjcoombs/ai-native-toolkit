"""Moved to ``lib.hosting.platform_cli`` (#917). This path is removed in 5.0.0."""
import sys

import lib.hosting.platform_cli as _moved

sys.modules[__name__] = _moved
