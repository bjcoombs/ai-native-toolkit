"""Moved to ``lib.manifest.manifest_whys`` (#917). This path is removed in a later major release."""
import sys

import lib.manifest.manifest_whys as _moved

sys.modules[__name__] = _moved
