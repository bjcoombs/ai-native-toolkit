"""Moved to ``lib.keyhole.accretion_ratchet`` (#917). This path is removed in 5.0.0."""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lib.keyhole.accretion_ratchet as _moved  # noqa: E402

sys.modules[__name__] = _moved

if __name__ == "__main__":
    runpy.run_module("lib.keyhole.accretion_ratchet", run_name="__main__")
