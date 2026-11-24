"""Moved to ``lib.agents.agent_building_vocab`` (#917). This path is removed in a later major release."""
import sys

import lib.agents.agent_building_vocab as _moved

sys.modules[__name__] = _moved
