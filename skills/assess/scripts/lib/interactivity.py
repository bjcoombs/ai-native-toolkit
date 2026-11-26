"""Moved to ``lib.run.interactivity`` (#917). This path is removed in 5.0.0."""
import sys

import lib.run.interactivity as _moved

sys.modules[__name__] = _moved
