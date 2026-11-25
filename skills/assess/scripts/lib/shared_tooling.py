"""Moved to ``lib.ci.shared_tooling`` (#917). This path is removed in 5.0.0."""
import sys

import lib.ci.shared_tooling as _moved

sys.modules[__name__] = _moved
