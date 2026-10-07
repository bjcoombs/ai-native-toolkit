"""Tests for stats sidecar diff."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.stats_diff import (
    diff_stats,
    hotspot_commits,
    load_stats,
)


def test_hotspot_commits_reads_commits_then_legacy_churn() -> None:
    """`commits` is the current field; `churn` is the legacy producer name a
    seeded/older prior snapshot may still carry (issue #47, observation 5)."""
    assert hotspot_commits({"commits": 12}) == 12
    assert hotspot_commits({"churn": 7}) == 7        # legacy fallback
    assert hotspot_commits({"commits": 3, "churn": 99}) == 3  # commits wins
    assert hotspot_commits({}) == 0
    assert hotspot_commits({"commits": None, "churn": None}) == 0


@pytest.fixture
def prior_stats(fixtures_dir: Path) -> dict:
    return load_stats(fixtures_dir / "prior_stats.json")


@pytest.fixture
def current_stats(fixtures_dir: Path) -> dict:
    return load_stats(fixtures_dir / "current_stats.json")


def test_load_stats_returns_dict(fixtures_dir: Path) -> None:
    stats = load_stats(fixtures_dir / "prior_stats.json")
    assert stats["files_scored"] == 100


def test_load_stats_missing_returns_none(tmp_path: Path) -> None:
    assert load_stats(tmp_path / "nope.json") is None


def test_diff_identifies_graduated(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    graduated_paths = {h.path for h in diff.graduated}
    assert "src/legacy/parser.go" in graduated_paths


def test_diff_identifies_regressed(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    regressed_paths = {h.path for h in diff.regressed}
    assert "src/api/handler.go" in regressed_paths
    # Regression must capture the delta
    handler = next(h for h in diff.regressed if h.path == "src/api/handler.go")
    assert handler.ccn_delta == 4  # 32 - 28
    assert handler.commits_delta == 7  # 15 - 8


def test_diff_identifies_new(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    new_paths = {h.path for h in diff.new}
    assert "src/new/feature.go" in new_paths


def test_diff_identifies_persistent(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    persistent_paths = {h.path for h in diff.persistent}
    assert "src/util/helpers.go" in persistent_paths


def test_diff_no_prior_means_all_new(current_stats: dict) -> None:
    diff = diff_stats(prior=None, current=current_stats)
    assert len(diff.graduated) == 0
    assert len(diff.regressed) == 0
    assert len(diff.persistent) == 0
    assert len(diff.new) == len(current_stats["top_hotspots"])


def test_diff_summary_counts(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    summary = diff.summary()
    assert summary["graduated"] == 1
    assert summary["regressed"] == 1
    assert summary["new"] == 1
    assert summary["persistent"] == 1
    assert summary["restructured"] == 0


def _one(path: str, *, ccn: float, max_fn: float | None, loc: int = 500,
         commits: int = 10) -> dict:
    h: dict = {"path": path, "ccn": ccn, "loc": loc, "commits": commits}
    if max_fn is not None:
        h["max_fn_ccn"] = max_fn
    return {"top_hotspots": [h]}


def _status(prior: dict, current: dict) -> str:
    diff = diff_stats(prior=prior, current=current)
    for name in ("regressed", "restructured", "persistent"):
        if getattr(diff, name):
            return name
    raise AssertionError("hotspot in both snapshots landed in no category")


def test_aggregate_up_worst_down_is_restructured_not_regressed() -> None:
    """Splitting a function into helpers raises the sum; the worst fell."""
    status = _status(_one("a.py", ccn=100, max_fn=40),
                     _one("a.py", ccn=110, max_fn=15))
    assert status == "restructured"


def test_worst_function_up_is_regressed_even_when_aggregate_fell() -> None:
    assert _status(_one("a.py", ccn=100, max_fn=20),
                   _one("a.py", ccn=95, max_fn=25)) == "regressed"


def test_worst_unavailable_falls_back_to_aggregate_rule() -> None:
    """scc rows carry no max_fn_ccn: an aggregate rise regresses as before."""
    diff = diff_stats(prior=_one("a.go", ccn=100, max_fn=None),
                      current=_one("a.go", ccn=104, max_fn=None))
    assert [t.path for t in diff.regressed] == ["a.go"]
    assert diff.regressed[0].max_fn_ccn_delta is None


def test_worst_known_on_one_side_only_falls_back_to_aggregate_rule() -> None:
    assert _status(_one("a.py", ccn=100, max_fn=None),
                   _one("a.py", ccn=104, max_fn=12)) == "regressed"


def test_worst_flat_aggregate_up_is_regressed_as_accretion() -> None:
    """A new function beside an unchanged worst one is growth, not a refactor."""
    assert _status(_one("a.py", ccn=100, max_fn=16),
                   _one("a.py", ccn=104, max_fn=16)) == "regressed"


def test_both_flat_is_persistent() -> None:
    assert _status(_one("a.py", ccn=100, max_fn=16),
                   _one("a.py", ccn=100, max_fn=16)) == "persistent"


def test_small_worst_trim_with_large_aggregate_growth_is_regressed() -> None:
    """Trimming the worst 16 -> 15 while adding +100 summed ccn is accretion,
    not an extraction; restructured must not hide it from the gate."""
    assert _status(_one("a.py", ccn=100, max_fn=16),
                   _one("a.py", ccn=200, max_fn=15)) == "regressed"


def test_aggregate_rise_equal_to_worst_fall_is_restructured() -> None:
    assert _status(_one("a.py", ccn=100, max_fn=30),
                   _one("a.py", ccn=110, max_fn=20)) == "restructured"


def test_worst_down_aggregate_down_is_persistent() -> None:
    assert _status(_one("a.py", ccn=100, max_fn=30),
                   _one("a.py", ccn=90, max_fn=12)) == "persistent"


def test_worst_down_with_loc_churn_growth_is_restructured() -> None:
    """The LOC-and-churn branch no longer overrides a falling worst function."""
    assert _status(_one("a.py", ccn=100, max_fn=30, loc=500, commits=10),
                   _one("a.py", ccn=100, max_fn=12, loc=600, commits=14)
                   ) == "restructured"


def test_real_assess_core_split_is_restructured() -> None:
    """The run after #451 split build_run_context: assess_core.py went from
    238 summed / 51 worst to 249 summed / 12 worst, +62 LOC, +1 commit. The old
    aggregate rule called that a regression; it is the recommended refactor."""
    path = "skills/assess/scripts/assess_core.py"
    diff = diff_stats(
        prior=_one(path, ccn=238.0, max_fn=51.0, loc=1027, commits=61),
        current=_one(path, ccn=249.0, max_fn=12.0, loc=1089, commits=62),
    )
    assert diff.regressed == []
    (t,) = diff.restructured
    assert (t.ccn_delta, t.max_fn_ccn_delta, t.loc_delta) == (11.0, -39.0, 62)
    assert diff.summary()["restructured"] == 1
