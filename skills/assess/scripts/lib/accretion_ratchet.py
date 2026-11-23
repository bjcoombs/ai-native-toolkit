"""Moved to ``lib.keyhole.accretion_ratchet`` (#917). This path is removed in 4.0.0."""
import runpy
import sys

import lib.keyhole.accretion_ratchet as _moved

sys.modules[__name__] = _moved

if __name__ == "__main__":
    runpy.run_module("lib.keyhole.accretion_ratchet", run_name="__main__")
