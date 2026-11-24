"""Moved to ``lib.docs.ownership_parser`` (#917). This path is removed in a later major release."""
import sys

import lib.docs.ownership_parser as _moved

sys.modules[__name__] = _moved
