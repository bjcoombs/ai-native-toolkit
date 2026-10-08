"""Tests for lib/diff_reliability.py: whether the cross-run diff can be trusted
(version stamps, tool versions, schema changes).

Moved out of test_assess_core.py when assess_core's helpers split into
lib/ modules; shared repo-seeding helpers live in assess_core_helpers.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from assess_core import build_run_context
from lib import diff_reliability


def test_diff_is_reliable_pure_matrix() -> None:
    """Direct unit coverage of the pure reliability decision, independent of the
    full pipeline."""
    from lib.diff_reliability import _diff_is_reliable

    # Missing prior version stamp.
    assert _diff_is_reliable(None, "1.0.0", 1, 1) == (
        False, "version not stamped in prior snapshot"
    )
    # Unparseable version.
    ok, note = _diff_is_reliable("not-a-version", "1.0.0", 1, 1)
    assert ok is False and "unparseable" in note
    # Schema delta.
    assert _diff_is_reliable("1.0.0", "1.0.1", 1, 2) == (
        False, "schema version changed 1->2"
    )
    # Major delta.
    assert _diff_is_reliable("1.9.9", "2.0.0", 1, 1) == (
        False, "major version changed 1.9.9->2.0.0"
    )
    # Minor/patch only -> reliable.
    assert _diff_is_reliable("1.2.3", "1.5.0", 1, 1) == (True, None)
    assert _diff_is_reliable("1.2.3", "1.2.3", 1, 1) == (True, None)


def test_diff_unreliable_when_prior_snapshot_lacks_version_stamp(tmp_path: Path) -> None:
    """A prior snapshot that never stamped a plugin version can't establish
    comparability, so the diff is flagged unreliable to suppress phantom
    transitions (issue #47, observation 4). The note names the exact reason."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps({
        # No plugin_version: seeded by hand / written by an older plugin.
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 500, "ccn": 20, "commits": 5}],
    }))
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "plugin_version": "1.12.0",
        "files_scored": 55, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/new.go", "loc": 400, "ccn": 18, "commits": 4}],
    }))
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert ctx["diff_version_note"] == "version not stamped in prior snapshot"
    assert ctx["diff_trend_reset"] is False


def test_diff_reliable_when_plugin_versions_match(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    stats = {
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(stats))
    (assess_dir / "complexity-stats.json").write_text(json.dumps(stats))
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is True
    assert ctx["diff_version_note"] is None


def _seed_prior_current(
    tmp_path: Path, prior_extra: dict, current_extra: dict,
) -> Path:
    """Seed a repo with a prior and current stats sidecar sharing one hotspot,
    each merged with the caller's version/schema/tool stamps."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    base = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(
        json.dumps({**base, **prior_extra})
    )
    (assess_dir / "complexity-stats.json").write_text(
        json.dumps({**base, **current_extra})
    )
    return repo


def test_diff_reliable_across_patch_bump_keeps_gate_armed(tmp_path: Path) -> None:
    """A patch/minor plugin bump with an unchanged schema and toolchain keeps the
    diff reliable so the trend and regression gate stay armed."""
    stamp = {"schema_version": 1, "lizard_version": "1.23.0"}
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.0", **stamp},
        {"plugin_version": "1.54.1", **stamp},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is True
    assert ctx["diff_version_note"] is None
    assert ctx["diff_trend_reset"] is False


def test_diff_unreliable_on_lizard_version_change_names_tool(tmp_path: Path) -> None:
    """A complexity-backend version change voids the diff and names the tool, so
    a score shift from the tool isn't read as a real regression."""
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.22.0"},
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0"},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert "lizard" in ctx["diff_version_note"]
    assert "1.22.0->1.23.0" in ctx["diff_version_note"]
    assert ctx["diff_trend_reset"] is False


def test_diff_major_bump_resets_trend(tmp_path: Path) -> None:
    """A MAJOR plugin bump voids the diff and flags a trend reset, which the
    report discloses explicitly."""
    stamp = {"schema_version": 1, "lizard_version": "1.23.0"}
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", **stamp},
        {"plugin_version": "2.0.0", **stamp},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert ctx["diff_trend_reset"] is True
    assert "major version changed 1.54.1->2.0.0" in ctx["diff_version_note"]


def test_diff_unreliable_on_schema_change(tmp_path: Path) -> None:
    """A stats schema-version delta voids the diff: the sidecar shape the diff
    reads moved, so the comparison isn't trustworthy."""
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0"},
        {"plugin_version": "1.54.2", "schema_version": 2, "lizard_version": "1.23.0"},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert "schema version changed 1->2" in ctx["diff_version_note"]
    assert ctx["diff_trend_reset"] is False


def test_tool_versions_surface_in_run_context(tmp_path: Path) -> None:
    """The toolchain the snapshot was produced with is surfaced for the report
    and gate to reason about comparability."""
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0"},
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0",
         "scc_version": "3.7.0"},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["tool_versions"] == {"lizard": "1.23.0", "scc": "3.7.0"}
    assert ctx["prior_tool_versions"] == {"lizard": "1.23.0"}
    assert ctx["schema_version"] == 1


def test_stats_tool_versions_reads_every_backend_by_language_tool() -> None:
    """stats_tool_versions reads any `<tool>_version` stamp, not a fixed
    lizard/scc tuple, so a per-function backend added later is compared too;
    the layout and plugin stamps are not tools."""
    got = diff_reliability.stats_tool_versions({
        "lizard_version": "1.23.0", "scc_version": "3.7.0",
        "dart-scanner_version": "1", "schema_version": 4,
        "artifact_schema_version": "1", "plugin_version": "1.80.0",
        "tool_versions": {"x": "y"}, "scc_version_extra": "no",
    })
    assert got == {"lizard": "1.23.0", "scc": "3.7.0", "dart-scanner": "1"}
