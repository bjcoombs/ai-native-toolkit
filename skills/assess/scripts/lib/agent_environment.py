"""Moved to ``lib.agents.agent_environment`` (#917). This path is removed in 4.0.0."""
import sys

import lib.agents.agent_environment as _moved

sys.modules[__name__] = _moved
