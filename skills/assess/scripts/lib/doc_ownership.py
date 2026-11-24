"""Moved to ``lib.docs.doc_ownership`` (#917). This path is removed in a later major release."""
import sys

import lib.docs.doc_ownership as _moved

sys.modules[__name__] = _moved
