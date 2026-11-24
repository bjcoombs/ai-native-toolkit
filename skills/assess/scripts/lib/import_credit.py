"""Moved to ``lib.testing.import_credit`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.import_credit as _moved

sys.modules[__name__] = _moved
