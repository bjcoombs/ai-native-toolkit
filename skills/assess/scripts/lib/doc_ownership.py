"""Moved to ``lib.docs.doc_ownership`` (#917). This path is removed in 4.0.0."""
import sys

import lib.docs.doc_ownership as _moved

sys.modules[__name__] = _moved
