"""Tests for stats sidecar diff."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.stats_diff import (
    HotspotTransition,
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
