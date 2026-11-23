"""Moved to ``lib.fleet.fleet_labels`` (#917). This path is removed in 4.0.0."""
import runpy
import sys

import lib.fleet.fleet_labels as _moved

sys.modules[__name__] = _moved

if __name__ == "__main__":
    runpy.run_module("lib.fleet.fleet_labels", run_name="__main__")
