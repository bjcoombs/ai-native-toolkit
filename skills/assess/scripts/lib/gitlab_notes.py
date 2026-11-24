"""Moved to ``lib.hosting.gitlab_notes`` (#917). This path is removed in a later major release."""
import sys

import lib.hosting.gitlab_notes as _moved

sys.modules[__name__] = _moved
