"""Degrade by construction (#418 requirement 3, #623).

Every scan-table entry degrades to the unavailable marker unless it names a
gate reason from ``GATE_REASONS``. The entries that used to run unwrapped now
degrade; each is forced to raise here, and the run - and every reader of the
key downstream of the table - must carry on with the marker in its place.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from lib import scan_registry as reg

# The entries #623 moved behind the degrade wrapper.
NEWLY_DEGRADING = (
    "stale_hubs", "churn_degenerate", "untracked_instruction_files",
    "broken_instruction_refs", "sensitive_instruction_content",
    "ancestor_instruction_files", "instruction_file_size", "coverage_report",
    "mutation_not_run_cap", "test_focus", "promissory_markers", "accretion_ratchet",
    "gap_actions", "excluded_generated", "uninstall_instructions_path", "anomalies",
)


def test_every_opt_out_names_a_gate_reason():
    opted_out = [spec for spec in reg.SCANS if not spec.degrade]
    assert opted_out, "the setup entries opt out"
    stray = {spec.key: spec.gate_reason for spec in opted_out
             if spec.gate_reason not in reg.GATE_REASONS}
    assert stray == {}
    assert not hasattr(reg, "_UNWRAPPED")


def test_the_formerly_unwrapped_entries_degrade():
    by_key = {spec.key: spec for spec in reg.SCANS}
    assert [key for key in NEWLY_DEGRADING if not by_key[key].degrade] == []
    # Reclassified, not degraded: each normalises its own failure.
    assert by_key["test_pressure"].gate_reason == reg._SELF_DEGRADING
    assert by_key["plugin_version"].gate_reason == reg._SELF_DEGRADING


def _raising_table(key: str) -> tuple[reg.ScanSpec, ...]:
    def boom(*_args):
        raise RuntimeError("kaput")

    table = tuple(replace(spec, fn=boom) if spec.key == key else spec for spec in reg.SCANS)
    reg.validate(table)
    return table


@pytest.mark.parametrize("key", NEWLY_DEGRADING)
def test_a_raising_entry_degrades_and_every_reader_accepts_the_marker(key, tmp_path, monkeypatch):
    from assess_core import build_and_publish_run
    from assess_core_helpers import _minimal_repo
    from assess_gate import evaluate, format_verdict
    from assess_report import render_report
    from lib.assess_config import load_gate_config
    from lib.counts_only.content_free import counts_only, unclassified_strings
    from lib.testing.mutation_cap import layer6_cap_violation
    from lib.testing.test_focus import mutation_scope

    monkeypatch.setattr(reg, "SCANS", _raising_table(key))
    repo = _minimal_repo(tmp_path)
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-20", non_interactive=True)

    marker = {"available": False, "reason": f"{key} scan failed: kaput"}
    assert ctx[key] == marker
    # The publish step ran to the end and wrote the marker.
    written = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    assert written[key] == marker
    # Counts-only keeps the flag, drops the reason, and has a rule for every string.
    assert counts_only(ctx)[key] == {"available": False}
    assert unclassified_strings(ctx) == []
    # The deterministic readers downstream of the table.
    render_report(ctx, "repo")
    format_verdict(evaluate(ctx, load_gate_config(repo)))
    mutation_scope(ctx.get("test_focus"))
    # A failed cap reads as mutation not run: Layer 6 stays capped.
    if key == "mutation_not_run_cap":
        assert layer6_cap_violation(ctx, 0.9) is not None


def test_a_failed_churn_check_does_not_read_as_degenerate_history():
    from assess_report import _render_churn_window

    ctx = {"doc_staleness": {"churn_window": "6mo"},
           "churn_degenerate": {"available": False, "reason": "x scan failed: kaput"}}
    assert _render_churn_window(ctx) == "6mo"
    assert "flat" in _render_churn_window({**ctx, "churn_degenerate": True})


def test_a_failed_coverage_read_proposes_no_coverage_action():
    from lib.report.gap_actions import build_gap_actions

    hot = [{"path": "src/a.py"}]
    software = {"archetype": "software"}
    none_found = {"available": False, "source": "none found"}
    failed = {"available": False, "reason": "coverage_report scan failed: kaput"}
    assert [g["signal"] for g in build_gap_actions(none_found, None, hot, software)] == [
        "coverage_report"]
    assert build_gap_actions(failed, None, hot, software) == []


def test_a_failed_generated_exclusion_scan_is_disclosed_not_silent():
    from assess_gate import _format_generated_disclosure
    from assess_report import render_generated_disclosure

    marker = {"available": False, "reason": "excluded_generated scan failed: kaput"}
    assert "not disclosed" in _format_generated_disclosure(marker)
    assert "not disclosed" in render_generated_disclosure({"excluded_generated": marker})
    assert _format_generated_disclosure([]) == ""
    assert render_generated_disclosure({"excluded_generated": []}) == ""


@pytest.mark.parametrize("content", ["[1, 2]", "{not json", None])
def test_plugin_version_never_raises(content, tmp_path):
    from lib.run_setup import read_plugin_version

    manifest = tmp_path / "plugin.json"
    if content is None:
        manifest.mkdir()  # reading a directory raises an OSError other than FileNotFoundError
    else:
        manifest.write_text(content, encoding="utf-8")
    assert read_plugin_version(manifest) == "unknown"


def test_counts_only_keeps_a_marker_as_a_flag_under_path_keyed_and_dropped_keys():
    """A path-keyed block's marker keys are fields, not paths to hash, and a
    dropped text field that degraded still says it is unavailable."""
    from lib.counts_only.content_free import counts_only, unclassified_strings

    marker = {"available": False, "reason": "scan failed: /home/me/secret"}
    ctx = {"sensitive_instruction_content": marker, "ancestor_instruction_files": marker,
           "instruction_file_size": {"CLAUDE.md": {"lines": 3}}}
    out = counts_only(ctx, hash_paths=True, salt="s")
    assert out["sensitive_instruction_content"] == {"available": False}
    assert out["ancestor_instruction_files"] == {"available": False}
    assert list(out["instruction_file_size"]) != ["CLAUDE.md"]  # real path keys still hash
    assert unclassified_strings(ctx) == []
