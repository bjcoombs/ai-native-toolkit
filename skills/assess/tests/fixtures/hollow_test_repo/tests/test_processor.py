"""Hollow test: pins the implementation, not the contract.

It asserts only on the private `_last_processed_line` cursor. A correct refactor
that renames or removes the cursor breaks this test; a behavioural regression in
the public output (wrong case, dropped lines) sails straight past it. This is the
meridian resume-guard fingerprint that detect_assertion_on_internal flags.
"""
from src.processor import Processor


def test_process_advances_cursor():
    p = Processor()
    p.process(["a", "b", "c", "d", "e"])
    assert p._last_processed_line == 5
