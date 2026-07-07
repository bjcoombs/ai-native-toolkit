"""Tests for the shields.io endpoint badge (lib/badge.py) and its producers.

The badge is a self-description, so the contract under test is honesty: colours
are pure threshold functions, the fallback only claims what was measured, and a
deterministic-only run never downgrades a finalized score badge.
"""
from __future__ import annotations

import json
from pathlib import Path

from lib.badge import (
    badge_exists,
    concern_count_from_findings,
    fallback_badge,
    maturity_band,
    score_badge,
    score_color,
    write_badge,
)


def test_score_color_bands():
    """Hand-computed band edges: first matching floor wins."""
    assert score_color(8.0) == "brightgreen"
    assert score_color(7.0) == "brightgreen"
    assert score_color(6.5) == "green"
    assert score_color(5.5) == "green"
    assert score_color(4.0) == "yellowgreen"
    assert score_color(2.5) == "yellow"
    assert score_color(1.0) == "orange"
    assert score_color(0.5) == "red"
    assert score_color(0.0) == "red"


def test_score_badge_shape():
    badge = score_badge(7.0, "AI-Native")
    assert badge == {
        "schemaVersion": 1,
        "label": "AI-readiness",
        "message": "7.0/8 · AI-Native",
        "color": "brightgreen",
    }


def test_fallback_badge_clean_repo_is_green():
    badge = fallback_badge(0, 0)
    assert badge["message"] == "0 findings · 0 stale markers"
    assert badge["color"] == "green"


def test_fallback_badge_colors_scale_with_concerns():
    assert fallback_badge(1, 3)["color"] == "yellow"
    assert fallback_badge(2, 0)["color"] == "yellow"
    assert fallback_badge(3, 0)["color"] == "orange"


def test_concern_count_ignores_refactor_boundary_and_empty():
    findings = [
        {"name": "hidden_coupling", "paths": ["a.py"], "action": "x"},
        {"name": "lying_map", "paths": [], "action": "x"},
        {"name": "unactioned_intent", "paths": ["b.py"], "action": "x"},
        {"name": "refactor_boundary", "paths": ["safe/"], "action": "x"},
    ]
    assert concern_count_from_findings(findings) == 2


def test_write_and_exists_roundtrip(tmp_path: Path):
    assert not badge_exists(tmp_path)
    write_badge(tmp_path, score_badge(5.0, "Solid"))
    assert badge_exists(tmp_path)
    data = json.loads((tmp_path / "badge.json").read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1
    assert data["message"] == "5.0/8 · Solid"


def test_score_badge_knowledge_base_denominator():
    """A KB renormalises the denominator over its applicable layers (#224)."""
    badge = score_badge(2.5, "Knowledge Base · Solid", denominator=3)
    assert badge["message"] == "2.5/3 · Knowledge Base · Solid"
    # 2.5/3 = 0.833 -> green band (>= 0.6875), not the misleading 2.5/8 yellow.
    assert badge["color"] == "green"


def test_score_color_normalises_over_denominator():
    # Full marks on a KB denominator is brightgreen, not red.
    assert score_color(3.0, 3) == "brightgreen"
    assert score_color(0.0, 3) == "red"
    # Default denominator 8 reproduces the original absolute bands.
    assert score_color(7.0) == "brightgreen"
    assert score_color(7.0, 8) == "brightgreen"


def test_maturity_band_ladder():
    """The documented ladder: >=0.875 AI-Native, >=0.625 Solid, >=0.375 Basic,
    else Not Ready. Band edges hit exactly."""
    assert maturity_band(7.0) == "AI-Native"   # 0.875
    assert maturity_band(6.9) == "Solid"       # 0.8625
    assert maturity_band(5.0) == "Solid"       # 0.625
    assert maturity_band(4.9) == "Basic"       # 0.6125
    assert maturity_band(3.0) == "Basic"       # 0.375
    assert maturity_band(2.9) == "Not Ready"   # 0.3625
    assert maturity_band(0.0) == "Not Ready"


def test_maturity_band_normalises_over_denominator():
    # A KB scoring full marks earns AI-Native, not the misleading 2.5/8 read.
    assert maturity_band(3.0, 3) == "AI-Native"
    assert maturity_band(2.5, 3) == "Solid"   # 0.833


def test_score_badge_stamps_run_id_when_supplied():
    badge = score_badge(6.0, "Solid", run_id="20260707120000-abcdef01")
    assert badge["run_id"] == "20260707120000-abcdef01"
    # Message/colour are unchanged by the extra provenance field.
    assert badge["message"] == "6.0/8 · Solid"


def test_badge_run_id_omitted_by_default():
    """No run_id -> no key, so the badge dict is byte-identical to before."""
    assert "run_id" not in score_badge(6.0, "Solid")
    assert "run_id" not in fallback_badge(1, 0)


def test_fallback_badge_stamps_run_id_when_supplied():
    badge = fallback_badge(2, 1, run_id="20260707120000-abcdef01")
    assert badge["run_id"] == "20260707120000-abcdef01"
    assert badge["message"] == "2 findings · 1 stale markers"
