"""Tests for the shared maturity mean (issue #684): lib/fleet/fleet_maturity.py."""
from __future__ import annotations

import lib.fleet.fleet_maturity as fm


def test_mean_share_is_each_known_level_over_its_top_level():
    assert fm.mean_share([(1, 2), (1, 4)]) == (0.5 + 0.25) / 2
    assert fm.known_shares([(3, 3), (0, 5)]) == [1.0, 0.0]  # level 0 is a known level, never dropped


def test_unknown_levels_and_missing_top_levels_are_left_out_never_zero():
    assert fm.mean_share([(None, 3), (2, None), (2, 0), (1, 2)]) == 0.5
    assert fm.mean_share([(None, 3), (1, None)]) is None
    assert fm.mean_share([]) is None


def test_a_boolean_is_not_a_level():
    assert fm.known_shares([(True, 2), (1, True)]) == []  # type: ignore[list-item]


def test_the_row_set_is_team_rows():
    assert fm.SCOPE == "team"
