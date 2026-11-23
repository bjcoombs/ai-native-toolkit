"""Moved to ``lib.hosting.gitlab_ci_lint`` (#917). This path is removed in 4.0.0."""
import sys

import lib.hosting.gitlab_ci_lint as _moved

sys.modules[__name__] = _moved
