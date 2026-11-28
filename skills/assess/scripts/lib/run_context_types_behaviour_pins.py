"""Run-context shapes for the ``behaviour_pins`` block (#855).

A sibling of ``lib/run_context_types.py``, which is at its line budget. Built by
``lib/testing/behaviour_pins.py``.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict


class BehaviourPin(TypedDict):
    """One declaration or signature that shows a behaviour-pinning test practice.

    ``practice`` is one of ``lib.testing.behaviour_pins.PRACTICES``; ``arm`` is what matched,
    as a slug (a dependency name such as ``pytest-socket``, ``go-fuzz-test`` or
    ``scheduled-workflow``); ``path`` is the first file, in path order, that declares it.
    """

    practice: str
    arm: str
    path: str


class BehaviourPinsBlock(TypedDict):
    """``behaviour_pins``: test practices that pin behaviour beyond coverage.

    Evidence only: no condition reads it. ``lib.scoring.condition_evidence`` attaches
    ``practices`` to the tests-run-in-CI condition when that condition is met.
    """

    available: bool
    reason: NotRequired[str]   # scan_failed: the reader raised
    files_read: int            # declaration files and workflows read
    practices: list[str]       # each practice found once, strongest first (PRACTICES order)
    pins: list[BehaviourPin]   # one per practice and arm, in PRACTICES order, then arm
