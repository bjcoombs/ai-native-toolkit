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
