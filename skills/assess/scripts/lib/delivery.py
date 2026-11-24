"""Moved to ``lib.hosting.delivery`` (#917). This path is removed in a later major release."""
import sys

import lib.hosting.delivery as _moved

sys.modules[__name__] = _moved
