"""One maturity number for a repository or a group, shared by the fleet diagrams (issue #684).

The quadrant (``lib/fleet_quadrant.py``), the slope chart (``lib/fleet_slope.py``) and
the trend lines (``lib/fleet_trends.py``) all plot a mean maturity. They take it the
same way, here:

- **Unit.** Each row's level as a share of the row's top level (the number of levels
  the maturity model gives it), so rows with different level counts weigh alike.
- **Known rows only.** A row with no level (unknown) or no top level is left out of
  the mean, never counted as 0; with no such row the mean is unknown (None).
- **Row set.** ``team`` rows only (``SCOPE``). A team row is evaluated on a
  repository's own scans, so it is the one set the per-repository quadrant can
  measure; an ``org`` row reads the group's settings, which no single repository
  has. The group charts use the same set so the three read on one scale, and the
  layer bars (``lib/fleet_layer_bars.py``) draw team rows too.

Pure arithmetic: no I/O, no ordering dependence.
"""
from __future__ import annotations

from collections.abc import Iterable

SCOPE = "team"


def known_shares(cells: Iterable[tuple[int | None, int | None]]) -> list[float]:
    """Each ``(level, top)`` pair with a whole level and a positive top level, as a share."""
    return [level / top for level, top in cells
            if type(level) is int and type(top) is int and top > 0]


def mean_share(cells: Iterable[tuple[int | None, int | None]]) -> float | None:
    """The mean share of top level over the known ``(level, top)`` pairs; None when none is."""
    shares = known_shares(cells)
    return sum(shares) / len(shares) if shares else None
