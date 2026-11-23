"""Moved to ``lib.docs.vault_queries`` (#917). This path is removed in 4.0.0."""
import sys

import lib.docs.vault_queries as _moved

sys.modules[__name__] = _moved
