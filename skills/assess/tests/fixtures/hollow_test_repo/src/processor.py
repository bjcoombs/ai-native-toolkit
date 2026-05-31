"""Tiny line processor shared verbatim by the hollow and honest fixture repos.

The two repos differ ONLY in their test file: the hollow repo asserts on the
private `_last_processed_line` cursor (implementation detail); the honest repo
asserts on the public `process` return value (the contract). Same source, same
coverage - the only variable is what the test pins.
"""
from __future__ import annotations


class Processor:
    def __init__(self) -> None:
        self._last_processed_line = 0
        self.results: list[str] = []

    def process(self, lines: list[str]) -> list[str]:
        for i, line in enumerate(lines, start=1):
            self._last_processed_line = i
            self.results.append(line.upper())
        return self.results
