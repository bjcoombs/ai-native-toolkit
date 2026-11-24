"""Moved to ``lib.hosting.gitlab_cli`` (#917). This path is removed in a later major release."""
import sys

import lib.hosting.gitlab_cli as _moved

sys.modules[__name__] = _moved
