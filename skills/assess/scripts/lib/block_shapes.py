"""Moved to ``lib.counts_only.block_shapes`` (#917). This path is removed in a later major release."""
import sys

import lib.counts_only.block_shapes as _moved

sys.modules[__name__] = _moved
