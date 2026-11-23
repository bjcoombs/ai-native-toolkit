"""One maturity number for a repository or a group, shared by the fleet diagrams (issue #684).

The quadrant (``lib/fleet/fleet_quadrant.py``), the slope chart (``lib/fleet/fleet_slope.py``) and
the trend lines (``lib/fleet/fleet_trends.py``) all plot a mean maturity. They take it the
same way, here:

- **Unit.** Each row's level as a share of the row's top level (the number of levels
  the maturity model gives it), so rows with different level counts weigh alike.
- **Known rows only.** A row with no level (unknown) or no top level is left out of
  the mean, never counted as 0; with no such row the mean is unknown (None).
- **Row set.** ``team`` rows only (``SCOPE``). A team row is evaluated on a
  repository's own scans, so it is the one set the per-repository quadrant can
  measure; an ``org`` row reads the group's settings, which no single repository
  has. The group charts use the same set so the three read on one scale.

- **Lower bound (#697).** ``floor_mean`` is the floor-inclusive figure shown beside
  the known-only mean, never in its place: an unknown row with a confirmed floor
  (``lib.levels.floor_of``) joins at that floor's share. It is a lower bound over a
  larger row set, so it can sit below the known-only mean (a 1-of-4 floor row
  joining rows at their top). Plotted positions stay on ``mean_share``: an entry
  written before #697 has no floors, and a line drawn on this figure would move
  only because floors appeared.

Pure arithmetic: no I/O, no ordering dependence.

The one maturity mean the quadrant, the slope chart and the trend lines share (#684).
`mean_share(cells)` averages each known `(level, top)` pair's share of top level, leaving out
an unknown level or a missing top level (`None` when nothing is left, never 0);
`known_shares` lists them. `SCOPE` is the row set, `team` rows: the only rows a single
repository can be measured by, so the group charts use them too and the three read on one
scale. Pure arithmetic.
`floor_mean(cells)` (#697) over `(level, floor, top)` triples is the floor-inclusive lower bound printed beside the known-only mean as "at least"; `floor_shares` lists its shares and `has_floor` says whether any unknown row has a floor. Plotted positions stay on `mean_share`.
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


Cell = tuple[int | None, int | None, int | None]  # (level, floor, top)


def floor_shares(cells: Iterable[Cell]) -> list[float]:
    """Each ``(level, floor, top)`` triple's known level, else its floor, as a share of top level."""
    return known_shares((level if type(level) is int else floor, top) for level, floor, top in cells)


def floor_mean(cells: Iterable[Cell]) -> float | None:
    """The floor-inclusive mean share: a lower bound over rows with a level or a floor; None when none."""
    shares = floor_shares(cells)
    return sum(shares) / len(shares) if shares else None


def has_floor(cells: Iterable[Cell]) -> bool:
    """Some unknown row carries a floor, so ``floor_mean`` says more than ``mean_share``."""
    return any(type(level) is not int and type(floor) is int for level, floor, _ in cells)
