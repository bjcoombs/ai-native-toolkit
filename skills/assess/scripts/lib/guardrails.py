"""Moved to ``lib.instructions.guardrails`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.guardrails as _moved

sys.modules[__name__] = _moved
