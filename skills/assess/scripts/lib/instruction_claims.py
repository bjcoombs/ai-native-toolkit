"""Moved to ``lib.instructions.instruction_claims`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.instruction_claims as _moved

sys.modules[__name__] = _moved
