"""Moved to ``lib.testing.mutation_refresh`` (#917). This path is removed in 4.0.0."""
import sys

import lib.testing.mutation_refresh as _moved

sys.modules[__name__] = _moved
