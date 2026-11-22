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
