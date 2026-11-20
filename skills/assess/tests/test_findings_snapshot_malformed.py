"""A hand-edited ``findings.json`` degrades with a named reason, never a traceback (#668).

Every shape a person can leave the committed snapshot in reads as either no
prior snapshot or the snapshot of its usable entries, with one stderr line
naming the reason; the gate and the pipeline keep going.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_gate import main
from lib.artifact_schema import ASSESS_METHOD_VERSION
from lib.findings_snapshot import (
    FINDINGS_SNAPSHOT_FILE,
    build_snapshot,
    committed_prior,
    load_snapshot,
    method_change,
    read_snapshot,
)

# (id, file bytes, reason token). Each makes the file unable to establish a
# prior method version, so it reads as no snapshot.
NO_SNAPSHOT_CASES = [
    ("not_utf8", b'{"assess_method_version": 1, "findings": ["\xff"]}', "not_utf8"),
    ("latin1", '{"assess_method_version": 1, "findings": ["café"]}'.encode("latin-1"),
     "not_utf8"),
    ("empty_file", b"", "invalid_json"),
    ("truncated", b'{"assess_method_version": 1, "findi', "invalid_json"),
    ("overlong_integer",
     ('{"assess_method_version": ' + "1" * 5000 + ', "findings": []}').encode(),
     "invalid_json"),
    ("too_deep", b"[" * 100_000, "too_deep"),
    ("top_level_list", b"[]", "not_object"),
    ("top_level_string", b'"findings"', "not_object"),
    ("top_level_null", b"null", "not_object"),
    ("version_missing", b'{"findings": []}', "version_missing"),
    ("version_bool", b'{"assess_method_version": true, "findings": []}', "version_invalid"),
    ("version_float", b'{"assess_method_version": 1.5, "findings": []}', "version_invalid"),
    ("version_null", b'{"assess_method_version": null, "findings": []}', "version_invalid"),
    ("version_text", b'{"assess_method_version": "v12", "findings": []}', "version_invalid"),
    ("version_list", b'{"assess_method_version": [1], "findings": []}', "version_invalid"),
    ("findings_missing", b'{"assess_method_version": 1}', "findings_missing"),
    ("findings_object", b'{"assess_method_version": 1, "findings": {}}', "findings_not_list"),
    ("findings_int", b'{"assess_method_version": 1, "findings": 5}', "findings_not_list"),
    ("findings_null", b'{"assess_method_version": 1, "findings": null}', "findings_not_list"),
    ("findings_string", b'{"assess_method_version": 1, "findings": "lying_map"}',
     "findings_not_list"),
]


@pytest.mark.parametrize(
    ("body", "reason"), [c[1:] for c in NO_SNAPSHOT_CASES],
    ids=[c[0] for c in NO_SNAPSHOT_CASES])
def test_unusable_snapshot_is_none_with_a_named_reason(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], body: bytes, reason: str) -> None:
    (tmp_path / FINDINGS_SNAPSHOT_FILE).write_bytes(body)
    assert read_snapshot(tmp_path) == (None, reason)
    assert load_snapshot(tmp_path) is None
    err = capsys.readouterr().err
    assert f"is malformed ({reason}); treated as no prior snapshot." in err
    assert err.count("\n") == 1


# (id, findings list, expected snapshot findings). The version and list are
# sound, so the usable entries stand and the rest are skipped.
ENTRY_CASES = [
    ("paths_int", [{"name": "lying_map", "paths": 5}], [{"name": "lying_map", "paths": []}]),
    ("paths_string", [{"name": "lying_map", "paths": "a.py"}],
     [{"name": "lying_map", "paths": []}]),
    ("paths_object", [{"name": "lying_map", "paths": {"a.py": 1}}],
     [{"name": "lying_map", "paths": []}]),
    ("paths_null", [{"name": "lying_map", "paths": None}], [{"name": "lying_map", "paths": []}]),
    ("path_not_string", [{"name": "lying_map", "paths": ["a.py", 3, None, ["b.py"]]}],
     [{"name": "lying_map", "paths": ["a.py"]}]),
    ("entry_not_object", ["lying_map", 7, None, {"name": "x", "paths": []}],
     [{"name": "x", "paths": []}]),
    ("name_missing", [{"paths": ["a.py"]}, {"name": "x"}], [{"name": "x", "paths": []}]),
    ("name_not_string", [{"name": 5, "paths": []}, {"name": ["x"], "paths": []}], []),
    ("lone_surrogate", [{"name": "lying_map", "paths": ["\ud800.py", "ok.py"]},
                        {"name": "\udfff", "paths": []}],
     [{"name": "lying_map", "paths": ["ok.py"]}]),
]


@pytest.mark.parametrize(
    ("findings", "expected"), [c[1:] for c in ENTRY_CASES], ids=[c[0] for c in ENTRY_CASES])
def test_unusable_entries_are_skipped_with_a_named_reason(
        tmp_path: Path, capsys: pytest.CaptureFixture[str],
        findings: list[object], expected: list[dict[str, object]]) -> None:
    # ``json.dumps`` writes a lone surrogate as a ``\ud800`` escape, which is
    # how a hand edit would put one in the file.
    (tmp_path / FINDINGS_SNAPSHOT_FILE).write_text(
        json.dumps({"assess_method_version": 3, "findings": findings}), encoding="utf-8")
    snap, reason = read_snapshot(tmp_path)
    assert reason == "malformed_entries"
    assert snap == {"assess_method_version": 3, "findings": expected}
    assert load_snapshot(tmp_path) == snap
    assert "is malformed (malformed_entries); unusable entries skipped." in capsys.readouterr().err


def test_absent_and_clean_snapshots_are_silent(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert read_snapshot(tmp_path) == (None, None)
    (tmp_path / FINDINGS_SNAPSHOT_FILE).write_text(json.dumps(
        {"assess_method_version": 2, "findings": [{"name": "lying_map", "paths": ["a.py"]},
                                                  {"name": "x"}]}))
    assert read_snapshot(tmp_path) == (
        {"assess_method_version": 2, "findings": [{"name": "lying_map", "paths": ["a.py"]},
                                                  {"name": "x", "paths": []}]}, None)
    load_snapshot(tmp_path)
    assert capsys.readouterr().err == ""


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    """Some Windows editors save with a byte-order mark; the content is sound."""
    (tmp_path / FINDINGS_SNAPSHOT_FILE).write_bytes(
        b'\xef\xbb\xbf{"assess_method_version": 4, "findings": []}')
    assert read_snapshot(tmp_path) == ({"assess_method_version": 4, "findings": []}, None)


def test_snapshot_path_that_is_a_directory_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / FINDINGS_SNAPSHOT_FILE).mkdir()
    assert read_snapshot(tmp_path) == (None, "unreadable")


def test_recorded_prior_with_non_list_findings_does_not_raise(tmp_path: Path) -> None:
    """A hand-edited run-context ``prior_findings_snapshot`` reaches
    ``committed_prior`` and ``method_change`` without passing the loader."""
    for findings in (5, {"lying_map": []}, "lying_map"):
        prior = {"assess_method_version": 1, "findings": findings}
        ctx = {"assess_method_version": 2, "prior_findings_snapshot": prior,
               "derived_findings": [{"name": "lying_map", "paths": 5}]}
        assert committed_prior(ctx, tmp_path) == build_snapshot([], 1)
        change = method_change(ctx)
        assert change is not None
        assert change["appeared"] == [] and change["cleared"] == []


_SKIPPED_PRIOR = {"assess_method_version": 1, "findings": [
    {"name": "lying_map", "paths": []}, {"name": "lying_map", "paths": []}]}


@pytest.mark.parametrize(("body", "recorded"), [
    (b'{"assess_method_version": 1, "findings": [], "note": "\xff"}', None),
    (json.dumps({"assess_method_version": 1, "findings": [
        {"name": "lying_map", "paths": 5},
        {"name": "lying_map", "paths": ["\ud800.py"]}]}).encode(), _SKIPPED_PRIOR),
    (b"[" * 100_000, None),
], ids=["not_utf8", "paths_int_and_lone_surrogate", "too_deep"])
def test_gate_passes_on_a_hand_edited_committed_snapshot(
        git_repo, capsys: pytest.CaptureFixture[str], body: bytes,
        recorded: dict[str, object] | None) -> None:
    """The issue's complaint: the gate reads the committed file outside the
    run-context try, so a malformed one turned into a red gate. It now names
    the reason on stderr and passes."""
    import assess_core
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))
    commit("init")
    assert assess_core.main([str(repo), "--non-interactive"]) == 0
    capsys.readouterr()

    snap_path = repo / ".assess" / FINDINGS_SNAPSHOT_FILE
    snap_path.write_bytes(body)
    assert main([str(repo)]) == 0
    assert "is malformed (" in capsys.readouterr().err

    # The next pipeline run degrades the same way and rewrites a sound file.
    snap_path.write_bytes(body)
    assert assess_core.main([str(repo), "--non-interactive"]) == 0
    assert "is malformed (" in capsys.readouterr().err
    rewritten, reason = read_snapshot(repo / ".assess")
    assert reason is None and rewritten is not None
    assert rewritten["assess_method_version"] == ASSESS_METHOD_VERSION
    ctx = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    # A prior with a sound version records its usable entries; the rest is None.
    assert ctx["prior_findings_snapshot"] == recorded
