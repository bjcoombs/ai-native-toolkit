"""Moved to ``lib.instructions.command_index`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.command_index as _moved

sys.modules[__name__] = _moved
