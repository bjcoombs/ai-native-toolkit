"""Moved to ``lib.keyhole.structure_drift`` (#917). This path is removed in 4.0.0."""
import sys

import lib.keyhole.structure_drift as _moved

sys.modules[__name__] = _moved
