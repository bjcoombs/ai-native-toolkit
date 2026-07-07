"""Deliberately low cyclomatic-complexity file.

Two branch-free functions -> CCN 1 each, file-aggregate CCN 2 (lizard).
Used by the golden-SVG test to assert low ccn -> pale hue.
"""


def add(a, b):
    return a + b


def mul(a, b):
    return a * b
