"""Moved to ``lib.manifest.check_validation`` (#917). This path is removed in a later major release."""
import sys

import lib.manifest.check_validation as _moved

sys.modules[__name__] = _moved
