"""Moved to ``lib.instructions.agent_consistency`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.agent_consistency as _moved

sys.modules[__name__] = _moved
