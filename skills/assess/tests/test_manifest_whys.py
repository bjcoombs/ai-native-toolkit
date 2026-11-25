"""The required ``why`` on every check manifest entry (lib/manifest/manifest_whys.py, #939)."""
from __future__ import annotations

import copy
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

import lib.manifest.check_manifest as cm
import lib.manifest.manifest_whys as mw
from lib.scoring.levels_fields import TRUTH_TAGS

COND_KEY = "layer-5-ci.1:ci_checks.enforced_check_runs"
PLUMBING_SCANS = {"delivery", "gate_cost_estimate", "shared_tooling", "agent_building", "ai_usage",
                  "diff", "runtime", "derived_findings", "gap_actions"}


def _core() -> dict[str, Any]:
    return copy.deepcopy(cm.read_manifest())


def _entry(data: dict[str, Any], pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    return next(e for e in data["entries"] if pred(e))


def _cond(data: dict[str, Any], key: str = COND_KEY) -> dict[str, Any]:
    return _entry(data, lambda e: e["kind"] == "condition" and e["key"] == key)


def _collector_of(data: dict[str, Any], scan: str) -> dict[str, Any]:
    return _entry(data, lambda e: e["kind"] == "collector" and scan in e.get("headless", {}))


def _errors(data: dict[str, Any]) -> list[str]:
    try:
        cm.parse_manifest(data, "fixture")
    except cm.CheckManifestError as e:
        return [cm.format_error(x) for x in e.errors]
    return []


def test_core_manifest_is_version_2_and_every_entry_has_a_why() -> None:
    m = cm.load_manifest()
    assert m["manifest_version"] == 2
    assert all(mw.why_of(e) for e in cm.conditions(m) + cm.findings(m))  # type: ignore[operator]
    scans = {s: mw.scan_why(c, s) for c in cm.collectors(m) for s in c.get("headless", {})}
    assert scans and all(scans.values())
    assert {s for s, w in scans.items() if w == mw.PLUMBING} == PLUMBING_SCANS
    assert all(mw.tag_why(m, t)["why"] for t in TRUTH_TAGS)
    assert mw.condition_whys(m)[COND_KEY].startswith("This turns")


@pytest.mark.parametrize("name", ["cond", "finding", "scan", "tag", "empty", "long"])
def test_version_2_rejects_a_missing_or_bad_why_naming_the_entry(name: str) -> None:
    data = _core()
    if name == "cond":
        del _cond(data)["why"]
        want = "core.cond.layer-5-ci.1.enforced_check_runs).why: required"
    elif name == "finding":
        del _entry(data, lambda e: e.get("name") == "hidden_coupling")["why"]
        want = "core.finding.hidden_coupling).why: required"
    elif name == "scan":
        del _collector_of(data, "ci_checks")["whys"]["ci_checks"]
        want = "core.ci_checks).whys.ci_checks: required"
    elif name == "tag":
        del data["tags"]["behaviour"]["why"]
        want = "manifest.tags.behaviour.why: required"
    else:
        _cond(data)["why"] = "" if name == "empty" else "a" * 301
        want = "core.cond.layer-5-ci.1.enforced_check_runs).why: a why is a string of 1 to 300"
    errors = _errors(data)
    assert len(errors) == 1 and want in errors[0], errors


def test_version_1_loads_without_whys_and_rejects_any_why() -> None:
    data = _core()
    data["manifest_version"] = 1
    del data["tags"]
    for e in data["entries"]:
        for key in ("why", "why_source", "whys", "why_sources"):
            e.pop(key, None)
    m = cm.parse_manifest(copy.deepcopy(data))
    assert not any(mw.why_of(e) for e in m["entries"])
    assert mw.tag_why(m, "behaviour")["why"] == ""
    _cond(data)["why"] = "An agent reads this."
    errors = _errors(data)
    assert len(errors) == 1 and "enforced_check_runs).why: a version 1 manifest carries no why" \
        in errors[0], errors
    del _cond(data)["why"]
    data["tags"] = {"behaviour": {"why": "x"}}
    assert any("manifest.tags" in e for e in _errors(data))


def test_whys_are_required_whatever_the_owner() -> None:
    data = _core()
    del _cond(data)["why"]

    def rename(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: rename(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        if isinstance(value, str) and value.startswith(("core.", "core:")):
            return "acme" + value[4:]
        return value

    acme = rename(data) | {"owner": "acme"}
    errors = _errors(acme)
    assert any("acme.cond.layer-5-ci.1.enforced_check_runs).why: required" in e for e in errors)


def test_a_signal_carries_no_why() -> None:
    data = _core()
    _entry(data, lambda e: e["id"] == "core:ci_checks.enforced_check_runs")["why"] = "An agent reads this."
    errors = _errors(data)
    assert len(errors) == 1 and "core:ci_checks.enforced_check_runs).why: a signal carries no why" \
        in errors[0], errors


@pytest.mark.parametrize(("scan", "reader"), [
    ("ci_checks", None), ("agent_harness", "agent_environment"), ("doc_graph", "doc_staleness"),
    ("review_automation", "review_evidence"),
])
def test_the_plumbing_token_only_fits_a_scan_nothing_reads(scan: str, reader: str | None) -> None:
    data = _core()
    _collector_of(data, scan)["whys"][scan] = mw.PLUMBING
    _collector_of(data, scan).pop("why_sources", None)
    errors = _errors(data)
    assert len(errors) == 1 and f"whys.{scan}: 'exempt: plumbing'" in errors[0], errors
    if reader is None:
        assert f"reads {scan!r} directly" in errors[0]
    else:
        assert f"read indirectly: {reader} reads it" in errors[0]


def test_a_collectors_read_of_its_own_block_does_not_count() -> None:
    # run_header publishes diff and reads the private diff collector; diff stays plumbing.
    m = cm.load_manifest()
    assert mw.scan_why(_collector_of(dict(m), "diff"), "diff") == mw.PLUMBING
    assert not mw.reach(m["entries"])["read"].get(("core.run_header", "diff"))


@pytest.mark.parametrize("kind", ["condition", "finding", "tag"])
def test_the_plumbing_token_is_only_for_scans(kind: str) -> None:
    data = _core()
    if kind == "condition":
        _cond(data, "layer-0-instructions.1:layer_grades.instruction_file_present")["why"] = mw.PLUMBING
    elif kind == "finding":
        _entry(data, lambda e: e.get("name") == "hidden_coupling")["why"] = mw.PLUMBING
    else:
        data["tags"]["docs"]["why"] = mw.PLUMBING
    errors = _errors(data)
    assert len(errors) == 1 and "'exempt: plumbing' is allowed only on a scan's why" in errors[0]


@pytest.mark.parametrize(("text", "named"), [
    ("CI catches failing changes.", "'failing'"), ("Layer 5 runs the checks.", "'Layer 5'"),
])
def test_a_why_passes_the_vocabulary(text: str, named: str) -> None:
    data = _core()
    _cond(data)["why"] = text
    errors = _errors(data)
    assert len(errors) == 1 and "enforced_check_runs).why:" in errors[0] and named in errors[0]


def test_sources_sit_beside_a_real_why_and_are_urls_or_paths() -> None:
    data = _core()
    _cond(data)["why_source"] = "not a source"
    _collector_of(data, "delivery")["why_sources"] = {"delivery": "https://example.com"}
    data["tags"]["docs"]["why_source"] = "docs/what-we-measure.md"
    _collector_of(data, "ci_checks")["whys"]["not_a_scan"] = "An agent reads this."
    codes = [e["code"] for e in cm.validate_manifest(data)]
    assert sorted(codes) == ["SOURCE_WITHOUT_WHY", "WHY_NOT_SCAN", "WHY_SOURCE"], codes


def test_a_load_through_the_cli_names_the_entry() -> None:
    # MLOAD: a fresh process, exit code and message, as the #939 contract runs it.
    code = ("import sys; sys.path.insert(0, 'scripts'); import lib.manifest.check_manifest as cm;"
            "cm.load_manifest(); print('ok')")
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, encoding="utf-8",
                         errors="replace", check=False, cwd=cm.CORE_MANIFEST.parents[1])
    assert run.returncode == 0 and "ok" in run.stdout, run.stderr


def test_a_headless_variants_reads_count_as_reads() -> None:
    # A used collector whose headless variant reads a plumbing scan makes it read.
    data = _core()
    structure = _collector_of(data, "structure")
    structure["headless_variants"] = [*structure.get("headless_variants", []),
                                      {"block": "structure", "callable": "lib.x:f",
                                       "reads": ["delivery"], "stage": "post_offers"}]
    seen = mw.reach(data["entries"])
    assert "keyhole_blocks" in seen["read"].get(("core.delivery", "delivery"), [])
