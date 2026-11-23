"""The strict check-manifest loader and its structured errors (lib/manifest/check_manifest.py,
lib/manifest/check_validation.py; #792).

The Levenshtein and closest-match tables are ported with the code from
meridianhub/meridian ``services/control-plane/internal/validator/manifest_validator_test.go``
(``TestLevenshteinDistance``, ``TestFindClosestMatch``).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import lib.manifest.check_manifest as cm
from lib.manifest.check_validation import closest, errors_only, finding, format_error, levenshtein


@pytest.mark.parametrize(("a", "b", "want"), [
    ("", "", 0), ("a", "", 1), ("", "a", 1), ("kitten", "sitting", 3),
    ("quantity", "quanity", 1), ("position_keeping", "position_keepng", 1),
    ("GBP", "GBQ", 1), ("abc", "xyz", 3),
])
def test_levenshtein(a: str, b: str, want: int) -> None:
    assert levenshtein(a, b) == want


@pytest.mark.parametrize(("target", "want"), [
    ("quanity", "quantity"), ("amout", "amount"), ("instrumnt", "instrument"),
    ("completely_different_very_long_name", ""), ("", ""),
])
def test_closest(target: str, want: str) -> None:
    assert closest(target, ["quantity", "instrument", "bucket_id", "as_of", "amount"]) == want


def test_finding_and_format() -> None:
    e = finding("entries[0].stagee", "UNKNOWN_KEY", "unknown key", suggestion="stage",
                available=["stage", "reads"])
    assert e["available"] == ["reads", "stage"]
    assert format_error(e) == "[error] entries[0].stagee: unknown key (suggestion: stage)"
    plain = finding("p", "C", "m", severity="warning")
    assert "suggestion" not in plain and "available" not in plain
    assert format_error(plain) == "[warning] p: m"
    assert errors_only([e, plain]) == [e]


def _collector(key: str, **extra: Any) -> dict[str, Any]:
    return {"id": f"core.{key}", "kind": "collector", "title": key, "callable": "lib.x:f",
            "publishes": [key], "reads": ["repo_root"], "stage": "read_side", "revision": 1,
            **extra}


def _valid() -> dict[str, Any]:
    return {"manifest_version": 1, "owner": "core", "entries": [
        _collector("scan_a"),
        _collector("blocks", publishes=["blk", "other"], spread=True, reads=["scan_a"],
                   headless={"blk": "headless_variant", "other": "unregistered"},
                   headless_variants=[{"block": "blk", "callable": "lib.h:blk",
                                       "reads": ["repo_root"], "stage": "read_side"}],
                   shapes=[{"path": "blk", "type": "lib.t:Blk"}], privacy={"blk.reason": "drop"}),
        _collector("hidden", publishes=[], private=True, degrade=False, gate_reason="setup",
                   stage="setup"),
        {"id": "core:scan_a.flag", "kind": "signal", "title": "Flag", "path": "scan_a.flag",
         "type": "bool", "collector": "core.scan_a", "evidence": "head_text"},
        {"id": "core.cond.row-a.1.flag", "kind": "condition", "title": "Flag set",
         "key": "row-a.1:scan_a.flag", "fix": "Set the flag."},
        {"id": "core.finding.thing", "kind": "finding", "title": "Thing", "name": "thing",
         "order": 0, "action": "act", "mode": "refactor_safe", "concern": False,
         "inputs": ["blk.rows", "hidden"]},
    ]}


def _codes(data: dict[str, Any]) -> list[str]:
    return [e["code"] for e in cm.validate_manifest(data)]


def test_valid_manifest_parses_and_helpers_read_it() -> None:
    m = cm.parse_manifest(_valid())
    assert [cm.scan_key(c) for c in cm.collectors(m)] == ["scan_a", "blocks", "hidden"]
    assert cm.block_collectors(m) == {"scan_a": "core.scan_a", "blk": "core.blocks",
                                      "other": "core.blocks"}
    assert [s["path"] for s in cm.signals(m)] == ["scan_a.flag"]
    assert [c["key"] for c in cm.conditions(m)] == ["row-a.1:scan_a.flag"]
    assert [f["name"] for f in cm.findings(m)] == ["thing"]


def _mutate(path: list[Any], value: Any = None, delete: bool = False) -> dict[str, Any]:
    data = _valid()
    node: Any = data
    for step in path[:-1]:
        node = node[step]
    if delete:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return data


@pytest.mark.parametrize(("path", "value", "code"), [
    (["entries", 0, "stagee"], "setup", "UNKNOWN_KEY"),
    (["entries", 0, "stage"], "later", "WRONG_TYPE"),
    (["entries", 0, "revision"], 0, "WRONG_TYPE"),
    (["entries", 0, "revision"], True, "WRONG_TYPE"),
    (["entries", 0, "reads"], "repo_root", "WRONG_TYPE"),
    (["entries", 0, "reads"], [1], "WRONG_TYPE"),
    (["entries", 0, "privacy"], {"x": "secret"}, "WRONG_TYPE"),
    (["entries", 0, "privacy"], [], "WRONG_TYPE"),
    (["entries", 0, "shapes"], {"path": "x"}, "WRONG_TYPE"),
    (["entries", 0, "shapes"], [{"path": "x"}], "WRONG_TYPE"),
    (["entries", 0, "shapes"], [{"path": 1, "type": "lib.t:T"}], "WRONG_TYPE"),
    (["entries", 0, "title"], "x" * 121, "WRONG_TYPE"),
    (["entries", 0, "callable"], "not a callable", "WRONG_TYPE"),
    (["entries", 5, "thresholds"], {"t": "high"}, "WRONG_TYPE"),
    (["entries", 5, "thresholds"], {"t": 0.5}, None),
    (["entries", 0, "kind"], "rule", "UNKNOWN_KIND"),
    (["entries", 0], "not a table", "WRONG_TYPE"),
    (["entries", 0, "id"], "core.Bad", "WRONG_TYPE"),
    (["entries", 0, "id"], "other.scan_a", "BAD_ID"),
    (["entries", 3, "id"], "core:scan_a.other", "BAD_ID"),
    (["entries", 4, "id"], "core.row-a.1.flag", "BAD_ID"),
    (["entries", 4, "id"], "core.cond.row-a.1.any_name", None),
    (["entries", 4, "id"], "core.cond.", "BAD_ID"),
    (["entries", 0, "id"], "scan_a", "BAD_ID"),
    (["entries", 5, "id"], "core.finding.other", "BAD_ID"),
    (["manifest_version"], 3, "WRONG_TYPE"),
    (["entries"], {}, "WRONG_TYPE"),
    (["owner"], "Core", "WRONG_TYPE"),
])
def test_field_errors(path: list[Any], value: Any, code: str | None) -> None:
    codes = _codes(_mutate(path, value))
    assert codes == ([code] if code else []), codes


def test_missing_keys_are_reported() -> None:
    assert _codes(_mutate(["entries", 0, "stage"], delete=True)) == ["MISSING_KEY"]
    assert _codes(_mutate(["owner"], delete=True)) == ["MISSING_KEY"]


def test_unknown_key_suggests_the_nearest_field() -> None:
    [error] = cm.validate_manifest(_mutate(["entries", 0, "stagee"], "setup"))
    assert error["suggestion"] == "stage" and "stage" in error["available"]
    assert error["path"] == "entries[0] (core.scan_a).stagee"


@pytest.mark.parametrize(("index", "changes", "code"), [
    (2, {"spread": True}, "PRIVATE_AND_SPREAD"),
    (2, {"publishes": ["hidden"]}, "PRIVATE_PUBLISHES"),
    (1, {"publishes": [], "headless": {}, "headless_variants": [], "shapes": []}, "SPREAD_EMPTY"),
    (0, {"publishes": ["other_key"]}, "PUBLISHES_KEY"),
    (2, {"gate_reason": None}, "NO_GATE_REASON"),
    (0, {"offline_callable": "lib.x:g"}, "OFFLINE_NOT_NETWORK"),
    (0, {"platform_only": True}, "PLATFORM_ONLY_NOT_PASS"),
    (0, {"headless": {"nope": "registered"}}, "UNPUBLISHED_BLOCK"),
    (1, {"headless": {"blk": "registered", "other": "unregistered"}}, "VARIANT_MISMATCH"),
    (1, {"headless": {"other": "unregistered"}}, "VARIANT_MISMATCH"),
    (0, {"shapes": [{"path": "elsewhere", "type": "lib.t:T"}]}, "UNPUBLISHED_BLOCK"),
    (0, {"reads": ["nothing_reads_this"]}, "UNKNOWN_READ"),
    (0, {"reads": ["hidden"], "stage": "setup"}, "UNKNOWN_READ"),
    (1, {"stage": "setup"}, "LATE_READ"),
])
def test_collector_rules(index: int, changes: dict[str, Any], code: str) -> None:
    data = _valid()
    entry = data["entries"][index]
    for key, value in changes.items():
        if value is None:
            entry.pop(key)
        else:
            entry[key] = value
    assert code in _codes(data), _codes(data)


def test_duplicates_are_reported() -> None:
    data = _valid()
    data["entries"].append(copy.deepcopy(data["entries"][0]))
    assert _codes(data) == ["DUPLICATE_ID"]
    data = _valid()
    data["entries"].append(_collector("scan_b", publishes=["blk"], spread=True))
    assert _codes(data) == ["DUPLICATE_BLOCK"]
    data = _valid()
    data["entries"].append({**copy.deepcopy(data["entries"][5]), "id": "core.finding.two",
                            "name": "two"})
    assert _codes(data) == ["DUPLICATE_ORDER"]
    data = _valid()
    data["entries"].append({**copy.deepcopy(data["entries"][4]), "id": "core.cond.twin"})
    assert _codes(data) == ["DUPLICATE_KEY"]


def test_a_scan_key_may_not_shadow_a_core_input() -> None:
    data = _valid()
    data["entries"].append(_collector("repo_root", publishes=[], private=True))
    assert _codes(data) == ["SHADOWS_INPUT"]


@pytest.mark.parametrize(("index", "key", "value", "code"), [
    (3, "collector", "core.scan_z", "UNKNOWN_COLLECTOR"),
    (3, "collector", "core.cond.row-a.1.flag", "UNKNOWN_COLLECTOR"),
    (3, "collector", "core.blocks", "UNPUBLISHED_BLOCK"),
    (5, "inputs", ["nowhere.rows"], "UNKNOWN_INPUT"),
])
def test_references(index: int, key: str, value: Any, code: str) -> None:
    assert _codes(_mutate(["entries", index, key], value)) == [code]


def test_condition_reading_an_undeclared_signal() -> None:
    data = _mutate(["entries", 4, "key"], "row-a.1:scan_a.flgg")
    data["entries"][4]["id"] = "core.cond.row-a.1.flgg"
    [error] = cm.validate_manifest(data)
    assert error["code"] == "UNKNOWN_SIGNAL" and error["suggestion"] == "scan_a.flag"


def test_parse_raises_with_every_error() -> None:
    data = _mutate(["entries", 0, "stagee"], "x")
    data["entries"][1]["revisionn"] = 1
    with pytest.raises(cm.CheckManifestError) as info:
        cm.parse_manifest(data, "fixture.toml")
    assert len(info.value.errors) == 2
    assert str(info.value).startswith("fixture.toml: 2 check manifest error(s)")


def test_load_manifest_reports_toml_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("manifest_version = \n", encoding="utf-8")
    with pytest.raises(cm.CheckManifestError) as info:
        cm.load_manifest(bad)
    assert info.value.errors[0]["code"] == "TOML_ERROR"


def test_load_manifest_reads_the_core_manifest_by_default() -> None:
    m = cm.load_manifest()
    assert m["owner"] == "core" and m["manifest_version"] == cm.MANIFEST_VERSION
    assert cm.CORE_MANIFEST.as_posix().endswith("skills/assess/checks/core.toml")
