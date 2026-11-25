"""Moved to ``lib.report.wiki_writer`` (#917). This path is removed in 5.0.0."""
import sys

import lib.report.wiki_writer as _moved

sys.modules[__name__] = _moved
