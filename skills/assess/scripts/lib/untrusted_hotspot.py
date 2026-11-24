"""Moved to ``lib.testing.untrusted_hotspot`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.untrusted_hotspot as _moved

sys.modules[__name__] = _moved
