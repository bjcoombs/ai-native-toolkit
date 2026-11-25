"""Moved to ``lib.instructions.claude_config_fields`` (#917). This path is removed in 5.0.0."""
import sys

import lib.instructions.claude_config_fields as _moved

sys.modules[__name__] = _moved
