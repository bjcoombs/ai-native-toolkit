"""Moved to ``lib.instructions.claude_config`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.claude_config as _moved

sys.modules[__name__] = _moved
