"""Tests for decline-marker provenance + re-offer-on-major-bump (Task 12)."""
from __future__ import annotations

import json
from pathlib import Path

from lib.decline_markers import (
    build_decline_block,
    read_decline_markers,
)


def _assess(tmp_path: Path) -> Path:
    d = tmp_path / ".assess"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_marker(assess_dir: Path, tool: str, payload: object) -> None:
    p = assess_dir / f".no-{tool}"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")


# ── provenance recording ────────────────────────────────────────────────────

def test_provenance_recorded(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "mutmut", {
        "declined_by": "ben",
        "declined_at": "2026-07-07",
        "plugin_version": "1.54.4",
        "reason": "pure-docs repo",
    })
    markers = read_decline_markers(d, "1.54.4")
    assert len(markers) == 1
    m = markers[0]
    assert m.tool == "mutmut"
    assert m.path == ".no-mutmut"
    assert m.declined_by == "ben"
    assert m.declined_at == "2026-07-07"
    assert m.version == "1.54.4"
    assert m.reason == "pure-docs repo"
    assert m.reoffer is False


def test_block_shape(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "scc", {
        "declined_by": "ben", "declined_at": "2026-07-07",
        "plugin_version": "1.54.4",
    })
    block = build_decline_block(d, "1.54.4")
    assert set(block) == {"markers", "reoffer_mutation", "disclosures"}
    entry = block["markers"][0]
    assert set(entry) >= {
        "path", "tool", "declined_by", "declined_at", "version", "reason",
        "reoffer",
    }


# ── disclosure in report ────────────────────────────────────────────────────

def test_disclosure_names_user_and_date(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "mutmut", {
        "declined_by": "ben", "declined_at": "2026-07-07",
        "plugin_version": "1.54.4",
    })
    block = build_decline_block(d, "1.54.4")
    disclosure = block["disclosures"][0]
    assert "Mutation testing permanently declined by ben on 2026-07-07" in disclosure


def test_disclosure_legacy_unknown(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "scc", "")  # legacy empty touch file
    block = build_decline_block(d, "1.54.4")
    disclosure = block["disclosures"][0]
    assert "an unknown user" in disclosure
    assert "an unknown date" in disclosure


# ── re-offer on major bump ──────────────────────────────────────────────────

def test_major_bump_sets_reoffer(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "mutmut", {
        "declined_by": "ben", "declined_at": "2025-01-01",
        "plugin_version": "1.9.0",
    })
    block = build_decline_block(d, "2.0.0")
    assert block["reoffer_mutation"] is True
    assert block["markers"][0]["reoffer"] is True
    # A mutation tool IS re-offered (Step 2d), so its disclosure says so.
    assert "re-offer eligible" in block["disclosures"][0]


def test_minor_bump_no_reoffer(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "mutmut", {
        "declined_by": "ben", "declined_at": "2026-06-01",
        "plugin_version": "1.50.0",
    })
    block = build_decline_block(d, "1.54.4")
    assert block["reoffer_mutation"] is False
    assert block["markers"][0]["reoffer"] is False


def test_patch_bump_no_reoffer(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "stryker", {
        "declined_by": "ben", "declined_at": "2026-07-01",
        "plugin_version": "1.54.3",
    })
    block = build_decline_block(d, "1.54.4")
    assert block["reoffer_mutation"] is False


def test_reoffer_only_for_mutation_tools(tmp_path: Path) -> None:
    # An old-major dead-code linter decline must not trip reoffer_mutation.
    d = _assess(tmp_path)
    _write_marker(d, "vulture", {
        "declined_by": "ben", "declined_at": "2025-01-01",
        "plugin_version": "1.0.0",
    })
    block = build_decline_block(d, "2.0.0")
    assert block["reoffer_mutation"] is False
    # ...but the marker itself is still flagged reoffer-eligible for its own line.
    assert block["markers"][0]["reoffer"] is True
    # The disclosure must NOT claim "re-offer eligible": only mutation tools are
    # re-offered (Step 2d); Step 2b never re-asks a linter decline, so promising
    # a re-offer here would be a lying map.
    assert "re-offer eligible" not in block["disclosures"][0]


# ── legacy / malformed markers degrade gracefully ───────────────────────────

def test_legacy_empty_marker_no_crash(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "mutmut", "")
    markers = read_decline_markers(d, "2.0.0")
    assert len(markers) == 1
    m = markers[0]
    assert m.version is None
    assert m.declined_by is None
    # Legacy markers have no major to compare, so they are never auto-re-offered.
    assert m.reoffer is False


def test_non_json_marker_no_crash(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "scc", "declined by hand\n")
    markers = read_decline_markers(d, "1.54.4")
    assert markers[0].version is None
    assert markers[0].reason is None


def test_no_assess_dir(tmp_path: Path) -> None:
    assert read_decline_markers(tmp_path / ".assess", "1.54.4") == []
    block = build_decline_block(tmp_path / ".assess", "1.54.4")
    assert block["markers"] == []
    assert block["reoffer_mutation"] is False


def test_markers_sorted_stable(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "scc", "")
    _write_marker(d, "mutmut", "")
    _write_marker(d, "vulture", "")
    tools = [m.tool for m in read_decline_markers(d, "1.54.4")]
    assert tools == sorted(tools)


def test_malformed_version_no_reoffer(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    _write_marker(d, "mutmut", {
        "declined_by": "ben", "declined_at": "2025-01-01",
        "plugin_version": "not-a-version",
    })
    block = build_decline_block(d, "2.0.0")
    assert block["reoffer_mutation"] is False
