"""Tests for decline-marker provenance + re-offer-on-major-bump (Task 12), and declined backlog proposals (#813)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import assess_actions_render as render
from lib.report import backlog_items as bi
from lib.run.decline_markers import (
    PROPOSAL_DECLINES,
    build_decline_block,
    decline_record,
    live_proposal_declines,
    proposal_disclosure,
    read_proposal_declines,
    read_decline_markers,
    split_declined,
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


# ── declined backlog proposals (#813) ───────────────────────────────────────

MODEL = "default-2026.11.24"
NEW_MODEL = "default-2026.12.01"
HEAD = "a" * 40
CID = "layer-5-ci.1"


def _tracker(tmp_path: Path) -> Path:
    """One open item marked for ``CID``: a close proposal whenever the condition reads met."""
    body = f"**Condition:** `{CID}`\n\n{bi.condition_marker(CID, MODEL)}\n"
    item = {"number": 7, "title": "Run the tests in CI", "body": body, "state": "OPEN",
            "labels": [{"name": bi.DEFAULT_LABEL}]}
    existing = tmp_path / "existing.json"
    existing.write_text(json.dumps([item]), encoding="utf-8")
    return existing


def _run_context(tmp_path: Path, state: str, model: str = MODEL) -> None:
    ctx = {
        "interactive": True,
        "level": {"levels_version": model, "conditions": [
            {"id": CID, "row": "layer-5-ci", "level": 1, "grade": "verified", "counted": True, "state": state,
             "why": "CI runs the suite on every push"}]},
        "measured_commit": {"head_sha": HEAD, "dirty": False},
        "since_last_run": {"previous_commit": "b" * 40},
        "prior_level": {"prior_level_version": 3, "levels_version": model, "tags": []},
    }
    d = _assess(tmp_path)
    (d / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")


def _proposed(tmp_path: Path, existing: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 0
    out: dict[str, Any] = json.loads(capsys.readouterr().out)
    return out


def _decline_all(tmp_path: Path, existing: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> None:
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--confirm", "none",
                        "--record-declines", "--declined-by", "ben", *extra]) == 0
    assert json.loads(capsys.readouterr().out) == []


def _kinds(out: dict[str, Any]) -> list[tuple[str, int]]:
    return [(p["kind"], p["number"]) for p in out["proposals"]]


def test_a_declined_close_is_not_proposed_on_the_next_run(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _tracker(tmp_path)
    _run_context(tmp_path, "met")
    assert _kinds(_proposed(tmp_path, existing, capsys)) == [("close", 7)]
    _decline_all(tmp_path, existing, capsys)
    out = _proposed(tmp_path, existing, capsys)
    assert out["proposals"] == []
    assert len(out["declined"]) == 1 and out["declined"][0].startswith("close #7 (`layer-5-ci.1`) declined by ben")
    assert "Declined before" in out["summary"] and "close #7" in out["summary"]


def test_a_model_bump_reproposes_once_and_a_second_decline_suppresses_it(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _tracker(tmp_path)
    _run_context(tmp_path, "met")
    _decline_all(tmp_path, existing, capsys)
    _run_context(tmp_path, "met", model=NEW_MODEL)
    assert _kinds(_proposed(tmp_path, existing, capsys)) == [("close", 7)]
    _decline_all(tmp_path, existing, capsys)
    assert _proposed(tmp_path, existing, capsys)["proposals"] == []
    [record] = read_proposal_declines(tmp_path / ".assess")
    assert record["model"] == NEW_MODEL


def test_a_change_in_the_condition_state_reproposes_it(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _tracker(tmp_path)
    _run_context(tmp_path, "met")
    _decline_all(tmp_path, existing, capsys)
    _run_context(tmp_path, "unmet")  # the condition lapsed: no close, and the decline no longer holds
    assert _proposed(tmp_path, existing, capsys)["proposals"] == []
    assert read_proposal_declines(tmp_path / ".assess") == []
    _run_context(tmp_path, "met")  # it holds again: new evidence, so the close comes back
    assert _kinds(_proposed(tmp_path, existing, capsys)) == [("close", 7)]


def test_the_record_carries_who_when_the_model_and_why(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _tracker(tmp_path)
    _run_context(tmp_path, "met")
    _decline_all(tmp_path, existing, capsys, "--decline-reason", "the CI job is not trusted yet")
    raw = json.loads((tmp_path / ".assess" / PROPOSAL_DECLINES).read_text(encoding="utf-8"))
    [record] = raw["declines"]
    assert record["declined_by"] == "ben"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["declined_at"])
    assert record["model"] == MODEL
    assert (record["kind"], record["number"], record["condition"], record["state"]) == ("close", 7, CID, "met")
    assert record["evidence"].startswith("`layer-5-ci.1` reads met (verified)")
    assert record["reason"] == "the CI job is not trusted yet"
    assert "reason: the CI job is not trusted yet" in proposal_disclosure(read_proposal_declines(tmp_path / ".assess")[0])


def test_a_confirmed_proposal_is_not_recorded_and_no_flag_writes_nothing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _tracker(tmp_path)
    _run_context(tmp_path, "met")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--confirm", "none"]) == 0
    capsys.readouterr()
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--confirm", "7",
                        "--record-declines"]) == 0
    assert [w["number"] for w in json.loads(capsys.readouterr().out)] == [7]
    assert not (tmp_path / ".assess" / PROPOSAL_DECLINES).exists()


def test_proposal_declines_degrade_and_keep_what_the_run_cannot_read(tmp_path: Path) -> None:
    d = _assess(tmp_path)
    assert read_proposal_declines(d) == []
    (d / PROPOSAL_DECLINES).write_text("not json", encoding="utf-8")
    assert read_proposal_declines(d) == []
    (d / PROPOSAL_DECLINES).write_text(json.dumps({"declines": [{"kind": "close"}, "x", {
        "kind": "demote", "number": 4, "finding": "self_referential_tests", "model": MODEL}]}), encoding="utf-8")
    [record] = read_proposal_declines(d)
    assert record["finding"] == "self_referential_tests" and record["declined_by"] is None
    assert "an unknown user on an unknown date" in proposal_disclosure(record)
    # No level result, or a condition this run's model does not list: the record stands.
    assert live_proposal_declines([record], None, None) == [record]
    cond = decline_record({"kind": "close", "number": 7, "condition": CID}, {CID: {"state": "met"}}, MODEL,
                          declined_by="ben", declined_at="2026-11-27")
    assert cond is not None and live_proposal_declines([cond], {}, MODEL) == [cond]
    assert decline_record({"number": 7}, None, MODEL, declined_by=None, declined_at=None) is None
    kept, held = split_declined([{"kind": "demote", "number": 4, "finding": "self_referential_tests"}],
                                [record], None, MODEL)
    assert kept == [] and held == [record]
