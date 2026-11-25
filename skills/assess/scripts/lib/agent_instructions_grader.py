"""Moved to ``lib.instructions.agent_instructions_grader`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.agent_instructions_grader as _moved

sys.modules[__name__] = _moved
