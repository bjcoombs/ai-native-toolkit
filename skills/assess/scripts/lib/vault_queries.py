"""Moved to ``lib.docs.vault_queries`` (#917). This path is removed in a later major release."""
import sys

import lib.docs.vault_queries as _moved

sys.modules[__name__] = _moved
