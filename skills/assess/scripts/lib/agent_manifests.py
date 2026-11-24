"""Moved to ``lib.agents.agent_manifests`` (#917). This path is removed in 5.0.0."""
import sys

import lib.agents.agent_manifests as _moved

sys.modules[__name__] = _moved
