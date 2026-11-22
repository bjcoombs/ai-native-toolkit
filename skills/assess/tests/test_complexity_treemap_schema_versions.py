"""ARTIFACT_SCHEMA_VERSION, STATS_SCHEMA_VERSION and tool-version stamps in
complexity-treemap.py's stats sidecar.

Every schema bump edits this module, and only this one: keep each version
assertion for the treemap family here. Split from test_complexity_treemap.py
(#720); the stubbed loader lives there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_complexity_treemap import _load_treemap


@pytest.fixture(scope="module")
def treemap():
    return _load_treemap()


def test_write_stats_stamps_schema_and_tool_versions(treemap, tmp_path, monkeypatch):
    """The sidecar stamps the stats schema version and the complexity backend
    versions so a later run can detect a schema or tool change and void a
    non-comparable diff. scc_version is present only when scc scored files."""
    monkeypatch.setattr(treemap, "_lizard_version", lambda: "1.23.0")
    monkeypatch.setattr(treemap, "_scc_version", lambda: "3.7.0")
    root = tmp_path
    lz = root / "a.go"
    sc = root / "b.rb"
    out = root / "stats.json"

    # lizard-only: no scc_version key.
    treemap.write_stats([(lz, 100, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text(encoding="utf-8"))
    assert stats["schema_version"] == treemap.STATS_SCHEMA_VERSION
    assert stats["lizard_version"] == "1.23.0"
    assert "scc_version" not in stats

    # A file scored by scc adds scc_version.
    treemap.write_stats(
        [(lz, 100, 5.0, "lizard"), (sc, 80, 4.0, "scc")], None, None, root, out
    )
    stats = json.loads(out.read_text(encoding="utf-8"))
    assert stats["lizard_version"] == "1.23.0"
    assert stats["scc_version"] == "3.7.0"


def test_tool_versions_omits_scc_when_not_scored(treemap, monkeypatch):
    """_tool_versions always carries lizard; scc appears only when a file was
    scored by scc AND scc is resolvable."""
    monkeypatch.setattr(treemap, "_lizard_version", lambda: "1.23.0")
    monkeypatch.setattr(treemap, "_scc_version", lambda: "3.7.0")
    assert treemap._tool_versions([(Path("a.go"), 1, 1.0, "lizard")]) == {
        "lizard": "1.23.0"
    }
    assert treemap._tool_versions([(Path("b.rb"), 1, 1.0, "scc")]) == {
        "lizard": "1.23.0", "scc": "3.7.0"
    }


def test_tool_versions_drops_scc_when_binary_absent(treemap, monkeypatch):
    """When scc scored files but the binary can't report a version, scc is
    omitted rather than stamped as a false 'unknown'."""
    monkeypatch.setattr(treemap, "_lizard_version", lambda: "1.23.0")
    monkeypatch.setattr(treemap, "_scc_version", lambda: None)
    assert treemap._tool_versions([(Path("b.rb"), 1, 1.0, "scc")]) == {
        "lizard": "1.23.0"
    }


def test_artifact_schema_version_mirrors_assess_core(treemap):
    """One /assess run stamps one provenance schema version on every artifact it
    writes. The treemap runs as its own process and duplicates the constant
    rather than importing lib.artifact_schema, so this is what keeps the copy honest:
    bump one without the other and complexity-stats.json disagrees with
    run-context.json about which run envelope it carries."""
    from lib import artifact_schema

    assert treemap.ARTIFACT_SCHEMA_VERSION == artifact_schema.ARTIFACT_SCHEMA_VERSION


def test_write_stats_stamps_run_id_and_schema_version(treemap, tmp_path):
    """The complexity-stats sidecar carries an artifact_schema_version and a
    unique run_id (assess-obey-thyself), so each stats emission is traceable.
    Distinct from the stats-layout `schema_version` (an int, versions the diff
    comparability)."""
    root = tmp_path
    f = root / "a.go"
    out = root / "stats.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text(encoding="utf-8"))
    assert stats["artifact_schema_version"] == treemap.ARTIFACT_SCHEMA_VERSION == "1.42.0"
    # The stats-layout schema_version (from #244) still coexists as an int.
    assert stats["schema_version"] == treemap.STATS_SCHEMA_VERSION
    run_id = stats["run_id"]
    stamp, _, suffix = run_id.partition("-")
    assert len(stamp) == 14 and stamp.isdigit()
    assert len(suffix) == 8

    out2 = root / "stats2.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], None, None, root, out2)
    assert json.loads(out2.read_text(encoding="utf-8"))["run_id"] != run_id


def test_stats_schema_version_raised_for_backend_by_language(treemap):
    assert treemap.STATS_SCHEMA_VERSION >= 4


def test_write_stats_version_keys_are_tool_versions_or_listed_non_tools(
        treemap, tmp_path, monkeypatch):
    """Every `*_version` key write_stats emits is either a tool version that
    diff_reliability.stats_tool_versions reads or a stamp listed in
    _NON_TOOL_VERSION_KEYS, so the writer and the reader cannot drift."""
    from lib import diff_reliability

    monkeypatch.setattr(treemap, "_scc_version", lambda: "3.7.0")
    root = tmp_path
    py, dart = root / "app.py", root / "order.dart"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (dart, 40, 30.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [7.0], dart: [12.0]},
        fn_backend_by_path={dart: "dart-scanner"},
    )
    stats = json.loads(out.read_text(encoding="utf-8"))
    version_keys = {k for k in stats if k.endswith("_version")}
    tools = {f"{t}_version" for t in diff_reliability.stats_tool_versions(stats)}
    assert tools == {"lizard_version", "scc_version"}
    assert version_keys - tools == set(diff_reliability._NON_TOOL_VERSION_KEYS)


def test_stats_schema_version_raised_for_dart_scanner(treemap):
    assert treemap.STATS_SCHEMA_VERSION >= 5
