"""Moved to ``lib.hosting.gitlab_notes`` (#917). This path is removed in 5.0.0."""
import sys

import lib.hosting.gitlab_notes as _moved

sys.modules[__name__] = _moved
