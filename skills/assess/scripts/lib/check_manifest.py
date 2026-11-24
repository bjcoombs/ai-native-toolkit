"""Moved to ``lib.manifest.check_manifest`` (#917). This path is removed in 5.0.0."""
import sys

import lib.manifest.check_manifest as _moved

sys.modules[__name__] = _moved
