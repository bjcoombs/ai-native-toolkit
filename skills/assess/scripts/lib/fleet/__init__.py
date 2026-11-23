"""The ``/fleet`` runner, roll-ups, run history, published fleet schemas and diagrams (#917).

Reading a runner name from this package is deprecated: the runner moved to
``lib.fleet.runner``, and this forward is removed in 4.0.0.
"""
import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    return getattr(importlib.import_module("lib.fleet.runner"), name)
