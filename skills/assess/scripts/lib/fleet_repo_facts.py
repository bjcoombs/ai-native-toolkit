"""Moved to ``lib.fleet.fleet_repo_facts`` (#917). This path is removed in 4.0.0."""
import sys

import lib.fleet.fleet_repo_facts as _moved

sys.modules[__name__] = _moved
