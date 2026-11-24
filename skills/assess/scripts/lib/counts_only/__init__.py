"""``lib.counts_only``: the content-free copy of a run-context and its allow-list (#917).

Reading a name of the old ``lib.counts_only`` module from this package is deprecated: it
moved to ``lib.counts_only.content_free``, and this forward is removed in a later major release.
"""
import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    return getattr(importlib.import_module("lib.counts_only.content_free"), name)
