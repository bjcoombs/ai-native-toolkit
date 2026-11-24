"""Moved to ``lib.manifest.manifest_whys`` (#917). This path is removed in 5.0.0."""
import sys

import lib.manifest.manifest_whys as _moved

sys.modules[__name__] = _moved
