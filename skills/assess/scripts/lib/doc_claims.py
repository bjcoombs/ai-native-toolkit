"""Moved to ``lib.docs.doc_claims`` (#917). This path is removed in 5.0.0."""
import sys

import lib.docs.doc_claims as _moved

sys.modules[__name__] = _moved
