"""Moved to ``lib.testing.mutation_refresh`` (#917). This path is removed in a later major release."""
import sys

import lib.testing.mutation_refresh as _moved

sys.modules[__name__] = _moved
