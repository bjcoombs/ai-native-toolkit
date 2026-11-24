"""Moved to ``lib.keyhole.archetype`` (#917). This path is removed in a later major release."""
import sys

import lib.keyhole.archetype as _moved

sys.modules[__name__] = _moved
