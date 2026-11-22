"""The fragment layout of a check manifest (lib/check_manifest.py, #982).

A root manifest that lists ``fragments`` loads one ``<root stem>/<name>.toml`` per
collector and merges them, by kind and in the listed order, into the same document a
single file declares. A single-file manifest (no ``fragments``) loads as before.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib import check_manifest as cm
from lib.check_validation import ValidationError

ROOT = ('manifest_version = 1\nowner = "core"\nfragments = [{names}]\n')
SCAN_A = '''[[entries]]
id = "core.scan_a"
kind = "collector"
title = "Scan A"
callable = "lib.x:f"
publishes = ["scan_a"]
reads = ["repo_root"]
stage = "read_side"
revision = 1

[[entries]]
id = "core:scan_a.flag"
kind = "signal"
title = "Flag"
path = "scan_a.flag"
type = "bool"
collector = "core.scan_a"
evidence = "head_text"

[[entries]]
id = "core.cond.row-a.1.flag"
kind = "condition"
title = "Flag set"
key = "row-a.1:scan_a.flag"
fix = "Set the flag."
'''
SCAN_B = '''[[entries]]
id = "core.scan_b"
kind = "collector"
title = "Scan B"
callable = "lib.x:g"
publishes = ["scan_b"]
reads = ["scan_a"]
stage = "read_side"
revision = 1
privacy = {"scan_b.reason" = "reason"}

[[entries]]
id = "core.finding.thing"
kind = "finding"
title = "Thing"
name = "thing"
order = 0
action = "act"
mode = "refactor_safe"
concern = false
inputs = ["scan_b.rows", "scan_a"]
'''


def _layout(tmp_path: Path, fragments: dict[str, str], names: list[str] | None = None) -> Path:
    root = tmp_path / "core.toml"
    listed = list(fragments) if names is None else names
    root.write_text(ROOT.format(names=", ".join(f'"{n}"' for n in listed)), encoding="utf-8")
    (tmp_path / "core").mkdir()
    for name, text in fragments.items():
        (tmp_path / "core" / f"{name}.toml").write_text(text, encoding="utf-8")
    return root


def _codes(root: Path) -> list[str]:
    with pytest.raises(cm.CheckManifestError) as info:
        cm.load_manifest(root)
    return [e["code"] for e in info.value.errors]


def test_fragments_merge_by_kind_in_the_listed_order(tmp_path: Path) -> None:
    root = _layout(tmp_path, {"scan_a": SCAN_A, "scan_b": SCAN_B})
    m = cm.load_manifest(root)
    assert set(m) == {"manifest_version", "owner", "entries"}
    assert [(e["kind"], e["id"]) for e in m["entries"]] == [
        ("collector", "core.scan_a"), ("collector", "core.scan_b"),
        ("signal", "core:scan_a.flag"), ("condition", "core.cond.row-a.1.flag"),
        ("finding", "core.finding.thing")]
    assert cm.privacy_rules(m) == {"scan_b": {"scan_b.reason": "reason"}}


def test_the_merged_document_is_the_single_file_document(tmp_path: Path) -> None:
    split = cm.load_manifest(_layout(tmp_path, {"scan_a": SCAN_A, "scan_b": SCAN_B}))
    single = tmp_path / "single.toml"
    head, signal = SCAN_A.split('[[entries]]\nid = "core:', 1)
    single.write_text('manifest_version = 1\nowner = "core"\n\n' + head + SCAN_B + '\n'
                      + '[[entries]]\nid = "core:' + signal, encoding="utf-8")
    flat = cm.load_manifest(single)
    assert json.dumps(sorted(flat["entries"], key=lambda e: e["kind"]), sort_keys=True) \
        == json.dumps(sorted(split["entries"], key=lambda e: e["kind"]), sort_keys=True)
    assert cm.collectors(flat) == cm.collectors(split)


def test_an_id_in_two_fragments_fails_naming_both_files(tmp_path: Path) -> None:
    twin = SCAN_B + "\n" + SCAN_A.split("\n\n", 2)[2]  # scan_b also declares the condition
    root = _layout(tmp_path, {"scan_a": SCAN_A, "scan_b": twin})
    with pytest.raises(cm.CheckManifestError) as info:
        cm.load_manifest(root)
    [error] = info.value.errors
    assert error["code"] == "DUPLICATE_ID"
    text = cm.format_error(error)
    assert "core/scan_a.toml" in text and "core/scan_b.toml" in text
    assert "core.cond.row-a.1.flag" in text


def test_a_single_file_duplicate_names_the_first_entry() -> None:
    data: dict[str, Any] = {"manifest_version": 1, "owner": "core", "entries": []}
    collector = {"id": "core.scan_a", "kind": "collector", "title": "A", "callable": "lib.x:f",
                 "publishes": ["scan_a"], "reads": [], "stage": "read_side", "revision": 1}
    data["entries"] = [collector, dict(collector)]
    [error] = cm.validate_manifest(data)
    assert error["code"] == "DUPLICATE_ID" and "entries[0]" in error["message"]


@pytest.mark.parametrize(("fragments", "names", "code"), [
    ({"scan_a": SCAN_A, "scan_b": SCAN_B}, ["scan_a"], "UNLISTED_FRAGMENT"),
    ({"scan_a": SCAN_A}, ["scan_a", "scan_b"], "MISSING_FRAGMENT"),
    ({"scan_a": SCAN_A}, ["scan_a", "scan_a"], "DUPLICATE_FRAGMENT"),
    ({"scan_a": SCAN_A}, ["Scan A"], "WRONG_TYPE"),
    ({"scan_c": SCAN_A}, None, "FRAGMENT_COLLECTOR"),
    ({"scan_a": SCAN_A + "\n" + SCAN_B}, None, "FRAGMENT_COLLECTOR"),
    ({"scan_a": "owner = \"core\"\n" + SCAN_A}, None, "UNKNOWN_KEY"),
    ({"scan_a": "entries = 1\n"}, None, "WRONG_TYPE"),
])
def test_layout_errors(tmp_path: Path, fragments: dict[str, str], names: list[str] | None,
                       code: str) -> None:
    assert code in _codes(_layout(tmp_path, fragments, names))


def test_a_root_lists_fragments_or_declares_entries_not_both(tmp_path: Path) -> None:
    root = _layout(tmp_path, {"scan_a": SCAN_A})
    root.write_text(root.read_text(encoding="utf-8") + "entries = []\n", encoding="utf-8")
    assert _codes(root) == ["FRAGMENTS_AND_ENTRIES"]


def test_a_toml_error_in_a_fragment_names_the_fragment(tmp_path: Path) -> None:
    root = _layout(tmp_path, {"scan_a": SCAN_A, "scan_b": "[[entries]\n"})
    with pytest.raises(cm.CheckManifestError) as info:
        cm.load_manifest(root)
    assert info.value.errors[0]["code"] == "TOML_ERROR"
    assert info.value.errors[0]["path"] == "core/scan_b.toml"


def test_a_validation_error_names_the_fragment(tmp_path: Path) -> None:
    root = _layout(tmp_path, {"scan_a": SCAN_A, "scan_b": SCAN_B.replace("revision = 1", "revision = 1\nrevisionn = 1")})
    with pytest.raises(cm.CheckManifestError) as info:
        cm.load_manifest(root)
    [error] = info.value.errors
    assert error["code"] == "UNKNOWN_KEY"
    assert error["path"].startswith("core/scan_b.toml entries[0] (core.scan_b)")


def test_read_manifest_returns_the_merged_document_unvalidated(tmp_path: Path) -> None:
    root = _layout(tmp_path, {"scan_a": SCAN_A, "scan_b": SCAN_B.replace("revision", "revisionn")})
    data = cm.read_manifest(root)
    assert [e["id"] for e in data["entries"]][:2] == ["core.scan_a", "core.scan_b"]


def test_the_core_manifest_is_one_fragment_per_collector() -> None:
    root = cm.read_manifest(cm.CORE_MANIFEST)
    m = cm.load_manifest()
    listed = cm.fragment_names(cm.CORE_MANIFEST)
    assert listed == [cm.scan_key(c) for c in cm.collectors(m)]
    on_disk = sorted(p.stem for p in cm.CORE_MANIFEST.with_suffix("").glob("*.toml"))
    assert on_disk == sorted(listed)
    assert root["entries"] == m["entries"]


SIGNAL_A = SCAN_A[SCAN_A.index('[[entries]]\nid = "core:scan_a.flag"'):SCAN_A.index('[[entries]]\nid = "core.cond')]
CONDITION_A = SCAN_A[SCAN_A.index('[[entries]]\nid = "core.cond'):]
FINDING_B = SCAN_B[SCAN_B.index('[[entries]]\nid = "core.finding.thing"'):]


def _owner_errors(root: Path) -> list[ValidationError]:
    with pytest.raises(cm.CheckManifestError) as info:
        cm.load_manifest(root)
    return [e for e in info.value.errors if e["code"] == "FRAGMENT_OWNER"]


@pytest.mark.parametrize(("moved", "into", "entry_id", "expected"), [
    (SIGNAL_A, "scan_b", "core:scan_a.flag", "core/scan_a.toml"),
    (FINDING_B, "scan_a", "core.finding.thing", "core/scan_b.toml"),
], ids=["signal", "finding"])
def test_an_entry_in_the_wrong_fragment_fails_with_fragment_owner(
        tmp_path: Path, moved: str, into: str, entry_id: str, expected: str) -> None:
    fragments = {"scan_a": SCAN_A.replace(moved, ""), "scan_b": SCAN_B.replace(moved, "")}
    fragments[into] += "\n" + moved
    [error] = _owner_errors(_layout(tmp_path, fragments))
    assert error["path"] == f"core/{into}.toml"
    assert entry_id in error["message"] and expected in error["message"]


def test_a_condition_belongs_with_the_signal_it_reads(tmp_path: Path) -> None:
    root = _layout(tmp_path, {"scan_a": SCAN_A.replace(CONDITION_A, ""),
                              "scan_b": SCAN_B + "\n" + CONDITION_A})
    [error] = _owner_errors(root)
    assert "core.cond.row-a.1.flag" in error["message"] and "core/scan_a.toml" in error["message"]


def test_a_finding_whose_first_input_is_a_scan_key_belongs_to_that_collector(tmp_path: Path) -> None:
    private_b = SCAN_B.replace('publishes = ["scan_b"]', "publishes = []\nprivate = true").replace(
        'privacy = {"scan_b.reason" = "reason"}\n', "").replace('"scan_b.rows"', '"scan_b"')
    (tmp_path / "ok").mkdir()
    (tmp_path / "moved").mkdir()
    assert cm.load_manifest(_layout(tmp_path / "ok", {"scan_a": SCAN_A, "scan_b": private_b}))
    moved = {"scan_a": SCAN_A + "\n" + FINDING_B.replace('"scan_b.rows"', '"scan_b"'),
             "scan_b": private_b[:private_b.index('[[entries]]\nid = "core.finding')]}
    [error] = _owner_errors(_layout(tmp_path / "moved", moved))
    assert "core/scan_b.toml" in error["message"]


def test_an_unresolvable_owner_is_left_to_validation(tmp_path: Path) -> None:
    root = _layout(tmp_path, {"scan_a": SCAN_A.replace('collector = "core.scan_a"', 'collector = "core.nope"'),
                              "scan_b": SCAN_B})
    assert "FRAGMENT_OWNER" not in _codes(root)


def test_the_core_manifest_rejects_a_signal_moved_to_another_collector(tmp_path: Path) -> None:
    src = cm.CORE_MANIFEST.with_suffix("")
    (tmp_path / "core").mkdir()
    (tmp_path / "core.toml").write_text(cm.CORE_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    texts = {p.name: p.read_text(encoding="utf-8") for p in src.glob("*.toml")}
    consistency = texts["agent_consistency.toml"]
    start = consistency.index('[[entries]]\nid = "core:agent_consistency.conflicts"')
    block = consistency[start:consistency.index("[[entries]]", start + 1)]
    texts["agent_consistency.toml"] = consistency.replace(block, "")
    texts["working_notes.toml"] += "\n" + block
    for name, text in texts.items():
        (tmp_path / "core" / name).write_text(text, encoding="utf-8")
    [error] = _owner_errors(tmp_path / "core.toml")
    assert error["path"] == "core/working_notes.toml"
    assert "core:agent_consistency.conflicts" in error["message"]
    assert "core/agent_consistency.toml" in error["message"]
