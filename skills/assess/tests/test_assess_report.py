"""Tests for the deterministic report renderer (assess_report.py).

The renderer is the frozen harness: same context in, same Markdown out, no LLM.
These tests pin its template substitution, the section renderers (hotspots,
keyhole summary, findings, diff), the conditional fallbacks, and the honest
boundary - it must NOT reproduce the LLM-only 0-8 score or Top 3 Actions.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_report import (
    _fmt,
    _render_commit_note,
    load_context,
    main,
    render_diff_section,
    render_findings_section,
    render_hotspots_table,
    render_keyhole_summary,
    render_report,
)


def _full_ctx() -> dict:
    """A realistic, non-normalised run-context with every section populated."""
    return {
        "run_date": "2026-06-01",
        "plugin_version": "1.22.0",
        "measured_commit": {
            "available": True,
            "head_short": "abc1234",
            "head_sha": "abc1234def5678",
            "subject": "feat: add the thing",
            "dirty": False,
            "behind": 0,
        },
        "prior_stats_exists": True,
        "diff_reliable": True,
        "diff_version_note": None,
        "stats_summary": {
            "files_scored": 58,
            "loc": {"max": 761.0, "p50": 132.5, "p95": 483.65, "total": 10428},
            "ccn": {"max": 169.0, "p50": 31.0, "p95": 107.35, "basis": "file-aggregate"},
            "top_hotspots": [
                {"path": "scripts/assess_core.py", "loc": 493, "ccn": 106.0, "commits": 14},
                {"path": "lib/doc_graph.py", "loc": 514, "ccn": 169.0, "commits": 7},
            ],
        },
        "diff": {"graduated": 1, "regressed": 1, "new": 1, "persistent": 1},
        "diff_detail": {
            "graduated": [{"path": "old/file.py", "ccn_delta": 0, "loc_delta": 0}],
            "new": [{"path": "new/file.py", "ccn_delta": 0, "loc_delta": 0}],
            "regressed": [{"path": "hot/file.py", "ccn_delta": 12, "loc_delta": 60}],
            "persistent": [{"path": "stable/file.py", "ccn_delta": 0, "loc_delta": 0}],
        },
        "doc_staleness": {"churn_window": "commits (last 12mo)", "available": True},
        "keyhole_summary": {
            "concerns": [{"name": "hidden_coupling", "count": 5}],
            "safe_zones": 1,
            "total_concerns": 5,
            "summary_text": "5 structural concerns (5 hidden coupling), 1 safe zone.",
        },
        "findings_markdown": (
            "## Cross-Layer Findings (Keyhole Readiness)\n\n"
            "### hidden_coupling\n\n"
            "Action: investigate the seam\n\n"
            "Paths:\n- scripts\n- scripts/tests\n"
        ),
        "prescribed_actions": [
            {"path": "scripts", "action": "investigate the seam",
             "findings": ["hidden_coupling"], "rank": 1},
        ],
    }


def _minimal_ctx() -> dict:
    """The thinnest valid context: first run, no hotspots, no findings."""
    return {
        "run_date": "2026-06-01",
        "plugin_version": "1.22.0",
        "prior_stats_exists": False,
        "stats_summary": {"files_scored": 0, "loc": {}, "ccn": {}, "top_hotspots": []},
    }


# --------------------------------------------------------------------------
# _fmt
# --------------------------------------------------------------------------

def test_fmt_none_is_question_mark() -> None:
    assert _fmt(None) == "?"


def test_fmt_whole_float_drops_decimal() -> None:
    assert _fmt(493.0) == "493"


def test_fmt_fractional_float_one_decimal() -> None:
    out = _fmt(107.35)
    assert out.startswith("107.")
    assert len(out.split(".")[1]) == 1


def test_fmt_int_passthrough() -> None:
    assert _fmt(14) == "14"


# --------------------------------------------------------------------------
# Full render
# --------------------------------------------------------------------------

def test_full_render_has_all_sections() -> None:
    report = render_report(_full_ctx(), "ai-native-toolkit")
    assert "# Deterministic Assessment Snapshot: ai-native-toolkit" in report
    assert "## Metrics Dashboard" in report
    assert "### Top Hotspots" in report
    assert "## Keyhole Readiness" in report
    assert "## Changes Since Last Run" in report
    # No unsubstituted placeholders leaked.
    assert "$" not in report


def test_full_render_metrics_values() -> None:
    report = render_report(_full_ctx(), "demo")
    assert "**Files scored:** 58" in report
    assert "**Total LOC:** 10428" in report
    assert "p95 LOC 483" in report
    assert "max 761" in report
    assert "p95 CCN 107" in report
    assert "max 169" in report
    assert "**Churn window:** commits (last 12mo)" in report


def test_full_render_substitute_is_strict() -> None:
    # render_report must not raise on a fully populated context.
    render_report(_full_ctx(), "demo")


def test_minimal_render_uses_fallbacks() -> None:
    report = render_report(_minimal_ctx(), "tiny")
    assert "_No hotspots identified._" in report
    assert "_No cross-layer findings recorded._" in report
    assert "_Keyhole readiness summary unavailable._" in report
    assert "first recorded snapshot" in report
    assert "$" not in report


# --------------------------------------------------------------------------
# Hotspots table
# --------------------------------------------------------------------------

def test_hotspots_table_empty_fallback() -> None:
    assert render_hotspots_table({"stats_summary": {"top_hotspots": []}}) == \
        "_No hotspots identified._"


def test_hotspots_table_rows_and_header() -> None:
    out = render_hotspots_table(_full_ctx())
    assert "| Path | LOC | CCN | Commits |" in out
    assert "| `scripts/assess_core.py` | 493 | 106 | 14 |" in out
    assert "| `lib/doc_graph.py` | 514 | 169 | 7 |" in out


def test_hotspots_table_missing_metric_renders_question_mark() -> None:
    ctx = {"stats_summary": {"top_hotspots": [{"path": "x.py"}]}}
    out = render_hotspots_table(ctx)
    assert "| `x.py` | ? | ? | ? |" in out


def test_hotspots_table_caps_at_ten_rows() -> None:
    hotspots = [{"path": f"f{i}.py", "loc": 1, "ccn": 1, "commits": 1} for i in range(25)]
    out = render_hotspots_table({"stats_summary": {"top_hotspots": hotspots}})
    # 2 header lines + 10 data rows.
    assert len(out.splitlines()) == 12
    assert "f9.py" in out
    assert "f10.py" not in out


# --------------------------------------------------------------------------
# Keyhole summary + findings
# --------------------------------------------------------------------------

def test_keyhole_summary_consumed_verbatim() -> None:
    out = render_keyhole_summary(_full_ctx())
    assert out == "5 structural concerns (5 hidden coupling), 1 safe zone."


def test_keyhole_summary_missing_fallback() -> None:
    assert render_keyhole_summary({}) == "_Keyhole readiness summary unavailable._"


def test_findings_section_verbatim() -> None:
    out = render_findings_section(_full_ctx())
    assert out.startswith("## Cross-Layer Findings (Keyhole Readiness)")
    assert "### hidden_coupling" in out
    assert "Action: investigate the seam" in out


def test_findings_section_empty_fallback() -> None:
    assert render_findings_section({"findings_markdown": "   "}) == \
        "_No cross-layer findings recorded._"
    assert render_findings_section({}) == "_No cross-layer findings recorded._"


# --------------------------------------------------------------------------
# Diff section
# --------------------------------------------------------------------------

def test_diff_no_prior_run() -> None:
    out = render_diff_section({"prior_stats_exists": False})
    assert "No prior run to compare against" in out


def test_diff_unreliable_is_suppressed_with_note() -> None:
    ctx = {
        "prior_stats_exists": True,
        "diff_reliable": False,
        "diff_version_note": "prior stats from plugin 1.10.0, current 1.22.0",
    }
    out = render_diff_section(ctx)
    assert out.startswith("_Diff suppressed:")
    assert "1.10.0" in out


def test_diff_unreliable_without_note_falls_back() -> None:
    out = render_diff_section({"prior_stats_exists": True, "diff_reliable": False})
    assert "not comparable" in out


def test_diff_reliable_renders_all_categories() -> None:
    out = render_diff_section(_full_ctx())
    assert "- **Graduated** (left the hotspot list): 1" in out
    assert "- **New** (entered the hotspot list): 1" in out
    assert "- **Regressed** (complexity or churn increased): 1" in out
    assert "- **Persistent** (still in the hotspot list): 1" in out
    assert "- `old/file.py`" in out
    assert "- `new/file.py`" in out
    assert "- `stable/file.py`" in out


def test_diff_regressed_shows_deltas() -> None:
    out = render_diff_section(_full_ctx())
    assert "- `hot/file.py` (CCN +12, LOC +60)" in out


def test_diff_regressed_without_deltas_omits_suffix() -> None:
    ctx = {
        "prior_stats_exists": True,
        "diff_reliable": True,
        "diff": {"regressed": 1},
        "diff_detail": {"regressed": [{"path": "x.py", "ccn_delta": 0, "loc_delta": 0}]},
    }
    out = render_diff_section(ctx)
    assert "- `x.py`" in out
    assert "(CCN" not in out


def test_diff_caps_rows_and_reports_overflow() -> None:
    entries = [{"path": f"f{i}.py"} for i in range(13)]
    ctx = {
        "prior_stats_exists": True,
        "diff_reliable": True,
        "diff": {"persistent": 13},
        "diff_detail": {"persistent": entries},
    }
    out = render_diff_section(ctx)
    assert "...and 3 more" in out
    assert "f9.py" in out
    assert "f10.py" not in out


# --------------------------------------------------------------------------
# Commit note
# --------------------------------------------------------------------------

def test_commit_note_clean() -> None:
    note = _render_commit_note(_full_ctx())
    assert "Measured at `abc1234`" in note
    assert '("feat: add the thing")' in note
    assert "dirty" not in note
    assert "behind" not in note


def test_commit_note_dirty_and_behind() -> None:
    ctx = {"measured_commit": {
        "available": True, "head_short": "deadbee", "dirty": True, "behind": 3,
    }}
    note = _render_commit_note(ctx)
    assert "working tree dirty" in note
    assert "3 commit(s) behind upstream" in note


def test_commit_note_unavailable_is_empty() -> None:
    assert _render_commit_note({"measured_commit": {"available": False}}) == ""
    assert _render_commit_note({}) == ""


# --------------------------------------------------------------------------
# Honest boundary: must NOT reproduce LLM-only content
# --------------------------------------------------------------------------

def test_report_omits_llm_only_artifacts() -> None:
    report = render_report(_full_ctx(), "demo")
    # The 0-8 layered score and the Top 3 Actions priority narrative are the
    # LLM's job; the frozen report names the boundary, it does not fake them.
    assert "## Top 3 Actions" not in report
    assert "present/partial/missing" not in report
    assert "/ 8" not in report
    assert "require LLM judgement" in report


# --------------------------------------------------------------------------
# main() + IO
# --------------------------------------------------------------------------

def _seed_run_context(repo_root: Path, ctx: dict) -> None:
    assess_dir = repo_root / ".assess"
    assess_dir.mkdir(parents=True, exist_ok=True)
    (assess_dir / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")


def test_load_context_roundtrip(tmp_path: Path) -> None:
    _seed_run_context(tmp_path, _full_ctx())
    loaded = load_context(tmp_path)
    assert loaded["run_date"] == "2026-06-01"


def test_main_writes_report_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_run_context(tmp_path, _full_ctx())
    rc = main([str(tmp_path)])
    assert rc == 0
    out_path = tmp_path / ".assess" / "deterministic-report.md"
    assert out_path.exists()
    report = out_path.read_text(encoding="utf-8")
    assert "# Deterministic Assessment Snapshot:" in report
    assert str(out_path) in capsys.readouterr().out


def test_main_stdout_does_not_write_file(tmp_path: Path,
                                         capsys: pytest.CaptureFixture) -> None:
    _seed_run_context(tmp_path, _full_ctx())
    rc = main([str(tmp_path), "--stdout"])
    assert rc == 0
    assert not (tmp_path / ".assess" / "deterministic-report.md").exists()
    out = capsys.readouterr().out
    assert "# Deterministic Assessment Snapshot:" in out


def test_main_no_args_usage_error(capsys: pytest.CaptureFixture) -> None:
    rc = main([])
    assert rc == 2
    assert "Usage:" in capsys.readouterr().err
