"""Deliberately high cyclomatic-complexity file.

One function, 20 `if` decision points -> file-aggregate CCN 21 (lizard).
Used by the golden-SVG test to assert ccn -> red hue.
"""


def classify(n):
    total = 0
    if n == 0:
        total += 1
    if n == 1:
        total += 2
    if n == 2:
        total += 3
    if n == 3:
        total += 4
    if n == 4:
        total += 5
    if n == 5:
        total += 6
    if n == 6:
        total += 7
    if n == 7:
        total += 8
    if n == 8:
        total += 9
    if n == 9:
        total += 10
    if n == 10:
        total += 11
    if n == 11:
        total += 12
    if n == 12:
        total += 13
    if n == 13:
        total += 14
    if n == 14:
        total += 15
    if n == 15:
        total += 16
    if n == 16:
        total += 17
    if n == 17:
        total += 18
    if n == 18:
        total += 19
    if n == 19:
        total += 20
    return total
