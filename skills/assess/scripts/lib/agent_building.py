"""Moved to ``lib.agents.agent_building`` (#917). This path is removed in a later major release."""
import sys

import lib.agents.agent_building as _moved

sys.modules[__name__] = _moved
