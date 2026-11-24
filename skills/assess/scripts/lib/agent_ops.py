"""Moved to ``lib.agents.agent_ops`` (#917). This path is removed in 5.0.0."""
import sys

import lib.agents.agent_ops as _moved

sys.modules[__name__] = _moved
