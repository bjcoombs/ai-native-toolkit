"""Moved to ``lib.hosting.gitlab_ci_lint`` (#917). This path is removed in a later major release."""
import sys

import lib.hosting.gitlab_ci_lint as _moved

sys.modules[__name__] = _moved
