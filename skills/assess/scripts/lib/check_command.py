"""Moved to ``lib.ci.check_command`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.check_command as _moved

sys.modules[__name__] = _moved
