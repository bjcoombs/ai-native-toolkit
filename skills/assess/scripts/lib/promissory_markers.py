"""Moved to ``lib.keyhole.promissory_markers`` (#917). This path is removed in 4.0.0."""
import runpy
import sys

import lib.keyhole.promissory_markers as _moved

sys.modules[__name__] = _moved

if __name__ == "__main__":
    runpy.run_module("lib.keyhole.promissory_markers", run_name="__main__")
