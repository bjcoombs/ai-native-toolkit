"""Tests for the shields.io endpoint badge (lib/badge.py).

The badge is a self-description, so the contract under test is honesty: its
message is the deterministic headline summary (the Step and the verified tally,
#747), its colour a pure function of the Step, and a run with no level result
claims no Step.
"""
from __future__ import annotations

import json
from pathlib import Path

from lib.badge import (
    badge_exists,
    headline_badge,
    step_color,
    write_badge,
    write_headline_badge,
)
from lib.headline import as_headline
from lib.level_result_types import Headline


def _headline(step: int | None = 3, summary: str = "Step 3 · 4 in place of 4 measured") -> Headline:
    return {"headline_version": 1, "available": True, "levels_version": "m1", "step": step,
            "next_step": None, "lines": [summary], "markdown": summary, "summary": summary}


def test_badge_message_is_the_headline_summary():
    badge = headline_badge(_headline())
    assert badge == {"schemaVersion": 1, "label": "AI-readiness",
                     "message": "Step 3 · 4 in place of 4 measured", "color": "green"}


def test_step_colours_rise_and_never_turn_red():
    colours = [step_color(s) for s in (1, 2, 3, 4, 5)]
    assert colours == ["blue", "blue", "green", "brightgreen", "brightgreen"]
    assert step_color(None) == "lightgrey"
    assert "red" not in colours


def test_unavailable_headline_claims_no_step():
    badge = headline_badge(as_headline(None))
    assert badge["message"] == "Step not measured"
    assert badge["color"] == "lightgrey"


def test_badge_carries_no_layered_score_or_band_name():
    message = headline_badge(_headline())["message"]
    assert "/ 8" not in message and "/8" not in message
    for band in ("Not Ready", "Basic", "Solid", "AI-Native"):
        assert band not in message


def test_run_id_and_scope_are_stamped_when_supplied():
    badge = headline_badge(_headline(), run_id="20260707120000-abcdef01", scope="services/api")
    assert badge["run_id"] == "20260707120000-abcdef01"
    assert badge["label"] == "AI-readiness (services/api)"
    assert "run_id" not in headline_badge(_headline())


def test_write_headline_badge_links_the_report(tmp_path: Path):
    write_headline_badge(tmp_path, _headline(), run_id="r1")
    data = json.loads((tmp_path / "badge.json").read_text(encoding="utf-8"))
    assert data["message"] == "Step 3 · 4 in place of 4 measured"
    assert data["link"] == "./assess-report.md"
    assert data["run_id"] == "r1"
    assert badge_exists(tmp_path)


def test_write_badge_is_utf8_json(tmp_path: Path):
    write_badge(tmp_path, headline_badge(_headline()))
    raw = (tmp_path / "badge.json").read_bytes()
    assert "·".encode() in raw
    assert json.loads(raw)["schemaVersion"] == 1
