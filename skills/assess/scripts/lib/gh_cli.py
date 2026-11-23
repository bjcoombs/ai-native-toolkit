"""Moved to ``lib.hosting.gh_cli`` (#917). This path is removed in 4.0.0."""
import sys

import lib.hosting.gh_cli as _moved

sys.modules[__name__] = _moved
