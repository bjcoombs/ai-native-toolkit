"""Moved to ``lib.docs.doc_claims`` (#917). This path is removed in a later major release."""
import sys

import lib.docs.doc_claims as _moved

sys.modules[__name__] = _moved
