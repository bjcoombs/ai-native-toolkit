"""Moved to ``lib.fleet.fleet_data_schema`` (#917). This path is removed in a later major release."""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lib.fleet.fleet_data_schema as _moved  # noqa: E402

sys.modules[__name__] = _moved

if __name__ == "__main__":
    runpy.run_module("lib.fleet.fleet_data_schema", run_name="__main__")
