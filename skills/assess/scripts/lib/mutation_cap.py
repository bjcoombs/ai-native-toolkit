"""Moved to ``lib.testing.mutation_cap`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.mutation_cap as _moved

sys.modules[__name__] = _moved
