"""Moved to ``lib.hosting.merged_sample`` (#917). This path is removed in a later major release."""
import sys

import lib.hosting.merged_sample as _moved

sys.modules[__name__] = _moved
