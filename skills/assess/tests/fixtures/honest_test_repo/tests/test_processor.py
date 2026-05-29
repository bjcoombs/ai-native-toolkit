"""Honest test: pins the contract, not the implementation.

It asserts on the public `process` return value - the observable behaviour the
caller depends on. A regression in the output is caught; a refactor that keeps
the output stable is free to change internals. detect_assertion_on_internal must
NOT flag this: there is no private-field assertion here.
"""
from src.processor import Processor


def test_process_uppercases_lines():
    p = Processor()
    out = p.process(["a", "b", "c", "d", "e"])
    assert out == ["A", "B", "C", "D", "E"]
