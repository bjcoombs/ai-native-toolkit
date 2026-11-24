"""Moved to ``lib.keyhole.archetype`` (#917). This path is removed in 5.0.0."""
import sys

import lib.keyhole.archetype as _moved

sys.modules[__name__] = _moved
