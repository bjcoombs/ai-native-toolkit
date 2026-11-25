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
from lib.scoring.headline import as_headline
from lib.scoring.level_result_types import Headline


def _headline(step: int | None = 3, summary: str = "Step 3 · next: coverage gate") -> Headline:
    return {"headline_version": 1, "available": True, "levels_version": "m1", "step": step,
            "next_step": None, "lines": [summary], "markdown": summary, "summary": summary}


def test_badge_message_is_the_headline_summary():
    badge = headline_badge(_headline())
    assert badge == {"schemaVersion": 1, "label": "AI-readiness",
                     "message": "Step 3 · next: coverage gate", "color": "green"}


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
    assert data["message"] == "Step 3 · next: coverage gate"
    assert data["link"] == "./assess-report.md"
    assert data["run_id"] == "r1"
    assert badge_exists(tmp_path)


def test_write_badge_is_utf8_json(tmp_path: Path):
    write_badge(tmp_path, headline_badge(_headline()))
    raw = (tmp_path / "badge.json").read_bytes()
    assert "·".encode() in raw
    assert json.loads(raw)["schemaVersion"] == 1


def test_a_measured_repository_below_the_first_step_is_not_grey() -> None:
    """#889: a custom model whose Step 1 has a gate; below it the badge takes Step 1's colour."""
    below = {**_headline(None, "no Step yet · next: own_gate"), "next_step": 1}
    assert headline_badge(below)["color"] == step_color(1) != step_color(None)  # type: ignore[arg-type]
    assert headline_badge(_headline(None, "no Steps defined"))["color"] == step_color(None)
