"""Tests for the committed findings snapshot and the method-version gate note (#554)."""
from __future__ import annotations

import json
from pathlib import Path

from assess_core import build_and_publish_run
from assess_core_helpers import _minimal_repo
from assess_gate import evaluate, format_verdict, main
from lib.artifact_schema import ASSESS_METHOD_VERSION
from lib.findings_snapshot import (
    FINDINGS_SNAPSHOT_FILE,
    build_snapshot,
    committed_prior,
    load_snapshot,
    method_change,
    write_snapshot,
)


def _ctx(prior: dict | None, current: list[dict], version: int = 2,
         scope_slug: str | None = None, scope: str | None = None) -> dict:
    return {
        "assess_method_version": version,
        "prior_findings_snapshot": prior,
        "derived_findings": current,
        "scope": scope,
        "scope_slug": scope_slug,
    }


# --- build / load / write ----------------------------------------------------

def test_build_snapshot_sorts_paths_and_keeps_run_order() -> None:
    snap = build_snapshot(
        [{"name": "lying_map", "paths": ["b.py", "a.py", "a.py"], "action": "x"},
         {"name": "hidden_coupling", "paths": []},
         {"paths": ["nameless.py"]}, "junk"],
        3,
    )
    assert snap == {
        "assess_method_version": 3,
        "findings": [
            {"name": "lying_map", "paths": ["a.py", "b.py"]},
            {"name": "hidden_coupling", "paths": []},
        ],
    }


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    snap = build_snapshot([{"name": "lying_map", "paths": ["a.py"]}], 1)
    write_snapshot(tmp_path, snap)
    text = (tmp_path / FINDINGS_SNAPSHOT_FILE).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert load_snapshot(tmp_path) == snap


def test_load_snapshot_absent_or_malformed_is_none(tmp_path: Path) -> None:
    assert load_snapshot(tmp_path) is None
    path = tmp_path / FINDINGS_SNAPSHOT_FILE
    for body in ("{not json", "[]", '{"findings": []}',
                 '{"assess_method_version": true, "findings": []}',
                 '{"assess_method_version": 1, "findings": {}}'):
        path.write_text(body, encoding="utf-8")
        assert load_snapshot(tmp_path) is None, body


# --- method_change -------------------------------------------------------------

def test_no_method_change_without_prior_snapshot() -> None:
    assert method_change(_ctx(None, [{"name": "lying_map", "paths": ["a.py"]}])) is None


def test_no_method_change_when_versions_match() -> None:
    prior = {"assess_method_version": 2, "findings": [{"name": "lying_map", "paths": []}]}
    assert method_change(_ctx(prior, [{"name": "lying_map", "paths": ["a.py"]}])) is None


def test_method_change_lists_appeared_and_cleared_by_path() -> None:
    prior = {"assess_method_version": 1, "findings": [
        {"name": "lying_map", "paths": ["keep.py", "gone.py"]},
        {"name": "dropped_finding", "paths": ["x.py"]},
    ]}
    current = [
        {"name": "lying_map", "paths": ["keep.py", "new.py"]},
        {"name": "unexplained_complexity", "paths": ["u.py"]},
    ]
    change = method_change(_ctx(prior, current))
    assert change is not None
    assert change["prior_method_version"] == 1
    assert change["method_version"] == 2
    assert change["appeared"] == [
        {"finding": "lying_map", "path": "new.py"},
        {"finding": "unexplained_complexity", "path": "u.py"},
    ]
    assert change["cleared"] == [
        {"finding": "dropped_finding", "path": "x.py"},
        {"finding": "lying_map", "path": "gone.py"},
    ]
    assert change["snapshot"] == ".assess/findings.json"
    assert change["downgrade"] is False
    assert ".assess/findings.json" in change["refreeze"]
    assert "assess_core.py" in change["refreeze"]
    # The same steps, in the same order, as the gate action's render step.
    assert (change["refreeze"].index("complexity-treemap.py")
            < change["refreeze"].index("assess_core.py"))


def test_older_method_than_snapshot_points_at_the_pin_not_a_refreeze() -> None:
    """CI pinned to an older toolkit than the one that wrote the snapshot:
    re-freezing would undo the newer method, so the remedy is the pin."""
    prior = {"assess_method_version": 3, "findings": [
        {"name": "lying_map", "paths": ["a.py"]}]}
    change = method_change(_ctx(prior, [], version=2))
    assert change is not None
    assert change["downgrade"] is True
    assert "uses: bjcoombs/ai-native-toolkit@v" in change["refreeze"]
    assert "method version 3 or later" in change["refreeze"]
    assert "assess_core.py" not in change["refreeze"]
    out = format_verdict(evaluate(_ctx(prior, [], version=2), {}))
    assert "from version 3 to 2" in out
    assert "older than the one that wrote the snapshot" in out
    assert "cleared (1):\n      lying_map: a.py" in out
    assert "Raise pin: raise the toolkit version" in out
    assert "Re-freeze:" not in out


def test_method_change_names_the_scoped_snapshot() -> None:
    prior = {"assess_method_version": 1, "findings": []}
    change = method_change(_ctx(prior, [], scope_slug="services-api",
                                scope="services/api"))
    assert change is not None
    assert change["snapshot"] == ".assess/services-api/findings.json"
    # The re-freeze command scopes both steps and writes into the slug directory.
    refreeze = change["refreeze"]
    assert "re-run /assess services/api" in refreeze
    assert refreeze.count("--scope services/api") == 2
    assert "--stats <repo_root>/.assess/services-api/complexity-stats.json" in refreeze
    assert "commit .assess/services-api/findings.json" in refreeze


def test_unscoped_refreeze_has_no_scope_flag() -> None:
    prior = {"assess_method_version": 1, "findings": []}
    change = method_change(_ctx(prior, []))
    assert change is not None
    assert "--scope" not in change["refreeze"]
    assert "--stats <repo_root>/.assess/complexity-stats.json" in change["refreeze"]


# --- gate rendering --------------------------------------------------------------

def test_gate_states_no_code_changed_and_prints_refreeze() -> None:
    prior = {"assess_method_version": 1, "findings": [
        {"name": "lying_map", "paths": ["gone.py"]}]}
    ctx = _ctx(prior, [{"name": "lying_map", "paths": ["new.py"]}])
    verdict = evaluate(ctx, {})
    out = format_verdict(verdict)
    assert "finding method changed from version 1 to 2" in out
    assert "not a code change" in out
    assert "this PR's included" in out
    assert "appeared (1):\n      lying_map: new.py" in out
    assert "cleared (1):\n      lying_map: gone.py" in out
    assert "Re-freeze:" in out and "assess_core.py" in out
    # Explanation only: a method change alone never fails the gate.
    assert verdict["failed"] is False


def test_gate_method_change_with_no_moves_says_none() -> None:
    prior = {"assess_method_version": 1, "findings": [{"name": "lying_map", "paths": ["a.py"]}]}
    out = format_verdict(evaluate(_ctx(prior, [{"name": "lying_map", "paths": ["a.py"]}]), {}))
    assert "appeared: none" in out and "cleared: none" in out


def test_gate_caps_listed_moves() -> None:
    prior = {"assess_method_version": 1, "findings": []}
    current = [{"name": "lying_map", "paths": [f"f{i:02}.py" for i in range(25)]}]
    out = format_verdict(evaluate(_ctx(prior, current), {}))
    assert "appeared (25):" in out
    assert "+5 more" in out


def test_gate_silent_when_versions_match() -> None:
    prior = {"assess_method_version": 2, "findings": []}
    out = format_verdict(evaluate(_ctx(prior, [{"name": "lying_map", "paths": ["a.py"]}]), {}))
    assert "method change" not in out


# --- end to end: assess_core writes the snapshot, the gate reads the mismatch -----

def test_core_writes_snapshot_and_stamps_method_version(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-19")
    assert ctx["assess_method_version"] == ASSESS_METHOD_VERSION
    assert ctx["prior_findings_snapshot"] is None
    snap = json.loads((repo / ".assess" / FINDINGS_SNAPSHOT_FILE).read_text())
    assert snap == build_snapshot(ctx["derived_findings"], ASSESS_METHOD_VERSION)
    # A second run with no change reads the first snapshot back and leaves the
    # file byte-identical, so the gate stays silent.
    before = (repo / ".assess" / FINDINGS_SNAPSHOT_FILE).read_bytes()
    ctx2 = build_and_publish_run(repo_root=repo, run_date="2026-11-20")
    assert ctx2["prior_findings_snapshot"] == snap
    assert (repo / ".assess" / FINDINGS_SNAPSHOT_FILE).read_bytes() == before
    assert method_change(ctx2) is None


def test_run_header_reserves_the_snapshot_key_for_the_publish_step() -> None:
    from lib import run_setup
    keys = run_setup.RUN_HEADER_KEYS
    assert keys.index("assess_method_version") == keys.index("artifact_schema_version") + 1
    assert "prior_findings_snapshot" in keys


def test_bumped_method_version_fixture_produces_gate_message(tmp_path: Path, capsys) -> None:
    repo = _minimal_repo(tmp_path)
    build_and_publish_run(repo_root=repo, run_date="2026-11-19")
    snap_path = repo / ".assess" / FINDINGS_SNAPSHOT_FILE
    # A snapshot committed under an older method, holding a finding path the
    # current method no longer produces.
    old = json.loads(snap_path.read_text())
    old["assess_method_version"] = ASSESS_METHOD_VERSION - 1
    old["findings"].append({"name": "retired_finding", "paths": ["src/old.py"]})
    snap_path.write_text(json.dumps(old))
    build_and_publish_run(repo_root=repo, run_date="2026-11-20")
    assert main([str(repo)]) == 0
    out = capsys.readouterr().out
    assert (f"from version {ASSESS_METHOD_VERSION - 1} to {ASSESS_METHOD_VERSION}"
            in out)
    assert "not a code change" in out
    assert "retired_finding: src/old.py" in out
    assert "Re-freeze:" in out
    # The run rewrote the snapshot at the current method version.
    assert json.loads(snap_path.read_text())["assess_method_version"] == ASSESS_METHOD_VERSION


def test_fail_on_failure_with_method_change_exits_1_and_explains(
        tmp_path: Path, capsys) -> None:
    """Issue #614: a frozen ``fail_on: [lying_map]`` that fires together with a
    snapshot from an older method fails the gate (exit 1) and still states the
    method change is not a code change."""
    assess = tmp_path / ".assess"
    assess.mkdir()
    (assess / "config.toml").write_text('[gate]\nfail_on = ["lying_map"]\n',
                                        encoding="utf-8")
    prior = {"assess_method_version": ASSESS_METHOD_VERSION - 1,
             "findings": [{"name": "lying_map", "paths": []}]}
    ctx = _ctx(prior, [{"name": "lying_map", "paths": ["src/app.py"]}],
               version=ASSESS_METHOD_VERSION)
    (assess / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert (f"from version {ASSESS_METHOD_VERSION - 1} to {ASSESS_METHOD_VERSION}"
            in out)
    assert "not a code change" in out
    assert "appeared (1):\n      lying_map: src/app.py" in out
    assert "FAIL" in out and "lying_map" in out


def test_gate_explains_method_change_against_the_committed_snapshot(
        git_repo, capsys) -> None:
    """Contract I554-2 (#661), the cold-verifier sequence: run the pipeline,
    then overwrite the committed findings.json with an older method version,
    one extra finding (``lying_map``: ``gone/old.py``) and one produced finding
    path removed. The gate, run directly against that committed snapshot with
    no fresh pipeline run, names the method change, lists the extra path as
    cleared and the removed one as appeared, and prints the re-freeze command."""
    import assess_core
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))
    (repo / "notes").mkdir()
    for i in range(12):
        (repo / "notes" / f"n{i}.md").write_text(f"# Note {i}\n")
    (repo / "CLAUDE.md").write_text("# KB\n\n<!-- assess-archetype: software -->\n")
    commit("init")
    assert assess_core.main([str(repo), "--non-interactive"]) == 0
    capsys.readouterr()

    snap_path = repo / ".assess" / FINDINGS_SNAPSHOT_FILE
    snap = json.loads(snap_path.read_text())
    produced = next(f for f in snap["findings"]
                    if f["name"] == "override_contradicts_signals")
    assert produced["paths"] == ["CLAUDE.md"]
    produced["paths"] = []
    lying = next((f for f in snap["findings"] if f["name"] == "lying_map"), None)
    if lying is None:
        lying = {"name": "lying_map", "paths": []}
        snap["findings"].append(lying)
    lying["paths"].append("gone/old.py")
    snap["assess_method_version"] = ASSESS_METHOD_VERSION - 1
    snap_path.write_text(json.dumps(snap, indent=2) + "\n")

    assert main([str(repo)]) == 0
    out = capsys.readouterr().out
    assert (f"from version {ASSESS_METHOD_VERSION - 1} to {ASSESS_METHOD_VERSION}"
            in out)
    assert "not a code change" in out
    assert "cleared (1):\n      lying_map: gone/old.py" in out
    assert "appeared (1):\n      override_contradicts_signals: CLAUDE.md" in out
    assert "Re-freeze:" in out and "assess_core.py <repo_root>" in out


def test_gate_reads_a_string_method_version_in_the_committed_snapshot(
        tmp_path: Path) -> None:
    """A hand-edited snapshot holding the method version as a numeric string
    still establishes the prior version rather than reading as no snapshot."""
    (tmp_path / FINDINGS_SNAPSHOT_FILE).write_text(
        json.dumps({"assess_method_version": "3", "findings": []}))
    snap = load_snapshot(tmp_path)
    assert snap is not None and snap["assess_method_version"] == 3


def test_committed_prior_prefers_an_older_on_disk_snapshot_in_the_scope_dir(
        tmp_path: Path) -> None:
    scoped = tmp_path / ".assess" / "svc"
    scoped.mkdir(parents=True)
    on_disk = build_snapshot([{"name": "lying_map", "paths": ["gone/old.py"]}], 1)
    write_snapshot(scoped, on_disk)
    recorded = build_snapshot([], 2)
    ctx = _ctx(recorded, [], version=2, scope_slug="svc", scope="svc")
    assert committed_prior(ctx, tmp_path) == on_disk


def test_committed_prior_keeps_the_recorded_prior_when_disk_is_current(
        tmp_path: Path) -> None:
    """In CI the pipeline has just rewritten findings.json at this run's
    version; the prior it recorded before the rewrite is the committed one."""
    (tmp_path / ".assess").mkdir()
    write_snapshot(tmp_path / ".assess", build_snapshot([], 2))
    recorded = build_snapshot([{"name": "lying_map", "paths": ["a.py"]}], 1)
    assert committed_prior(_ctx(recorded, [], version=2), tmp_path) == recorded
    assert committed_prior(_ctx(None, [], version=2), tmp_path) is None


def test_load_snapshot_non_integer_string_version_is_none(tmp_path: Path) -> None:
    """A hand-edited version string ``int`` cannot parse ("²", "v12", "") reads
    as no snapshot instead of crashing the gate."""
    for version in ("\u00b2", "v12", "", "1.5"):
        (tmp_path / FINDINGS_SNAPSHOT_FILE).write_text(
            json.dumps({"assess_method_version": version, "findings": []}))
        assert load_snapshot(tmp_path) is None, version
