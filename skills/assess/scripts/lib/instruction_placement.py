"""Moved to ``lib.instructions.instruction_placement`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.instruction_placement as _moved

sys.modules[__name__] = _moved
