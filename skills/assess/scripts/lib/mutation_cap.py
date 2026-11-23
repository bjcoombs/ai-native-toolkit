"""Moved to ``lib.testing.mutation_cap`` (#917). This path is removed in 4.0.0."""
import sys

import lib.testing.mutation_cap as _moved

sys.modules[__name__] = _moved
