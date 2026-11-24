"""Moved to ``lib.keyhole.structure_drift`` (#917). This path is removed in a later major release."""
import sys

import lib.keyhole.structure_drift as _moved

sys.modules[__name__] = _moved
