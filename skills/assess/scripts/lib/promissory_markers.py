"""Moved to ``lib.keyhole.promissory_markers`` (#917). This path is removed in 4.0.0."""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lib.keyhole.promissory_markers as _moved  # noqa: E402

sys.modules[__name__] = _moved

if __name__ == "__main__":
    runpy.run_module("lib.keyhole.promissory_markers", run_name="__main__")
