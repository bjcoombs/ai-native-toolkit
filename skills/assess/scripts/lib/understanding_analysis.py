"""Moved to ``lib.keyhole.understanding_analysis`` (#917). This path is removed in a later major release."""
import sys

import lib.keyhole.understanding_analysis as _moved

sys.modules[__name__] = _moved
