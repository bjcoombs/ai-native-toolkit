"""Each monorepo unit's Step and tally (lib/unit_levels.py, issue #864).

The #744 engine runs once per unit over the run context with the unit's own readings
(lib/unit_readings.py, #863) in place; inherited conditions count and are labelled.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib import core_scans as cs
from lib import unit_levels as ul
from lib.counts_only import counts_only
from lib.level_tally import LEVEL_READS, condition_ids, default_model, run_context_level
from lib.scan_registry import SCANS
from test_unit_readings import MONOREPO, _write


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from assess_core import build_run_context
    repo = _write(tmp_path_factory.mktemp("mono") / "repo", MONOREPO)
    ctx: dict[str, Any] = build_run_context(repo_root=repo, run_date="2026-11-22",
                                            non_interactive=True)
    return ctx


def _origin(unit: ul.UnitLevel, cid: str) -> str:
    return next(c["origin"] for c in unit["conditions"] if c["id"] == cid)


def _levels(readings: Any, repo: dict[str, Any]) -> ul.UnitLevelsBlock:
    return ul.unit_levels(readings, *[repo.get(b) for b in LEVEL_READS])


# --- the #800 fixture -------------------------------------------------------------

def test_the_fixture_gives_three_unit_results(run: dict[str, Any]) -> None:
    block = run["unit_levels"]
    assert block["available"] is True
    assert block["unit_count"] == 3
    assert [u["path"] for u in block["units"]] == ["services/billing", "services/search", "web"]
    for unit in block["units"]:
        assert isinstance(unit["step"], int)
        assert set(unit["tally"]) == {"met", "measured", "not_measured", "verified"}
        assert {c["id"] for c in unit["conditions"]} == set(condition_ids(default_model()))
    assert list(run).index("unit_levels") > list(run).index("unit_readings")


def test_the_two_inherited_conditions_are_labelled(run: dict[str, Any]) -> None:
    billing, search, web = run["unit_levels"]["units"]
    # billing has its own instruction file; CI is the root's for every unit
    assert billing["inherited"] == ["layer-5-ci.1"]
    assert _origin(billing, "layer-0-instructions.1") == "unit"
    for unit in (search, web):
        assert unit["inherited"] == ["layer-0-instructions.1", "layer-5-ci.1"]
        assert _origin(unit, "layer-0-instructions.1") == "inherited"
        assert _origin(unit, "layer-5-ci.1") == "inherited"


def test_each_unit_reads_its_own_scoped_conditions(run: dict[str, Any]) -> None:
    billing, search, _ = run["unit_levels"]["units"]
    state = {u["path"]: {c["id"]: c["state"] for c in u["conditions"]} for u in (billing, search)}
    # billing's docs are its own; search has none of its own to read
    assert state["services/billing"]["doc-freshness.1"] == "met"
    assert state["services/search"]["doc-freshness.1"] == "not_measured"
    assert billing["tally"]["met"] > search["tally"]["met"]
    # #1083: a Step reads its gates only, and both units hold the same gates (billing's own
    # instruction file, search's inherited one), so the larger tally moves no Step
    assert billing["step"] == search["step"]


def test_a_sibling_units_coverage_gate_is_not_inherited(run: dict[str, Any]) -> None:
    _, search, web = run["unit_levels"]["units"]
    for unit in (search, web):
        assert _origin(unit, "layer-6-coverage.1") == "unit"
        assert "layer-6-coverage.1" not in unit["inherited"]


# --- the overlay ---------------------------------------------------------------------

REPO: dict[str, Any] = {
    "layer_grades": {"available": True, "instruction_file_present": False,
                     "instruction_presence": "none"},
    "ci_checks": {"available": True},
}


def _reading(path: str, blocks: dict[str, Any], own: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"path": path, "blocks": blocks,
            "conditions": [{"id": cid, "origin": "unit" if cid in own else "inherited"}
                           for cid in sorted(condition_ids(default_model()))]}


def test_a_units_own_reading_replaces_the_repository_reading() -> None:
    lifted = _reading("a", {"layer_grades": {"available": True, "instruction_file_present": True}},
                      own=("layer-0-instructions.1",))
    plain = _reading("b", {})
    block = _levels({"available": True, "unit_count": 2, "units": [lifted, plain]}, REPO)
    a, b = block["units"]
    state = {u["path"]: {c["id"]: c["state"] for c in u["conditions"]} for u in (a, b)}
    assert state["a"]["layer-0-instructions.1"] == "met"
    assert state["b"]["layer-0-instructions.1"] == "unmet"
    assert a["inherited"] == [] and _origin(a, "layer-0-instructions.1") == "unit"
    # a unit with no readings of its own is the repository's result
    repo_level = run_context_level(REPO)
    assert b["step"] == repo_level["step"] and b["next_step"] == repo_level["next_step"]
    assert {k: b["tally"][k] for k in ("met", "measured", "not_measured")} == repo_level["tally"]


def test_an_inherited_condition_holds_a_step_gate() -> None:
    repo = {**REPO, "layer_grades": {"available": True, "instruction_file_present": True,
                                     "instruction_presence": "root"}}
    block = _levels({"available": True, "unit_count": 1, "units": [_reading("a", {})]}, repo)
    unit = block["units"][0]
    assert unit["inherited"][:1] == ["layer-0-instructions.1"]
    gates = [b.get("gate") for b in (unit["next_step"] or {"blockers": []})["blockers"]]
    assert "instructions" not in gates


def test_an_unavailable_own_reading_is_not_measured_never_unmet() -> None:
    broken = _reading("a", {"stats_summary": {"available": False, "reason": "no_lizard: x"}},
                      own=("layer-4-architecture.2",))
    unit = _levels({"available": True, "unit_count": 1, "units": [broken]}, REPO)["units"][0]
    cond = next(c for c in unit["conditions"] if c["id"] == "layer-4-architecture.2")
    assert cond["state"] == "not_measured"
    assert cond["reason"]


def test_owner_exclusions_apply_to_every_unit() -> None:
    repo = {**REPO, "owner_exclusions": {"exclusions": [{"id": "layer-0-instructions.1",
                                                          "kind": "other"}]}}
    block = _levels({"available": True, "unit_count": 1, "units": [_reading("a", {})]}, repo)
    cond = next(c for c in block["units"][0]["conditions"] if c["id"] == "layer-0-instructions.1")
    assert cond["state"] == "not_applicable"


# --- unknown stays unknown -------------------------------------------------------------

def test_a_null_unit_count_stays_null() -> None:
    readings = {"available": True, "unit_count": None, "unit_count_reason": "unreadable_manifest",
                "units": [_reading("a", {})]}
    block = _levels(readings, REPO)
    assert block["unit_count"] is None
    assert block["unit_count_reason"] == "unreadable_manifest"
    assert len(block["units"]) == 1


@pytest.mark.parametrize("readings", [None, {"available": False, "reason": "boom"}])
def test_unavailable_unit_readings_read_unavailable(readings: Any) -> None:
    block = _levels(readings, REPO)
    assert block["available"] is False
    assert block["unit_count"] is None
    assert block["units"] == []


# --- registry, manifest, counts-only ----------------------------------------------------

def test_the_registry_entry_reads_the_unit_readings_then_the_level_reads() -> None:
    spec = next(s for s in SCANS if s.key == "unit_levels")
    assert spec.reads == ul.READS == ("unit_readings", *LEVEL_READS)
    assert spec.fn is cs.unit_levels
    keys = [s.key for s in SCANS]
    assert keys.index("unit_readings") < keys.index("unit_levels")


def test_counts_only_drops_unit_paths_and_keeps_the_numbers(run: dict[str, Any]) -> None:
    block = run["unit_levels"]
    out = counts_only({"unit_levels": block})["unit_levels"]
    assert "services/billing" not in json.dumps(out)
    assert out["unit_count"] == 3
    for kept, unit in zip(out["units"], block["units"]):
        assert "path" not in kept
        assert kept["step"] == unit["step"]
        assert kept["tally"] == unit["tally"]
        assert kept["inherited"] == unit["inherited"]
        # the why is prose: counts-only drops it and keeps every token
        assert kept["conditions"] == [{k: v for k, v in c.items() if k != "why"}
                                      for c in unit["conditions"]]
        assert kept["next_step"] == unit["next_step"]


def test_the_root_unit_reads_the_repository_level(tmp_path: Path) -> None:
    repo = {**REPO, "layer_grades": {"available": True, "instruction_file_present": True,
                                     "instruction_presence": "root"}}
    root = _reading(".", {"layer_grades": {"available": True, "instruction_file_present": True}},
                    own=("layer-0-instructions.1",))
    unit = _levels({"available": True, "unit_count": 1, "units": [root]}, repo)["units"][0]
    assert unit["path"] == "."
    assert unit["step"] == run_context_level(repo)["step"]


# --- a root unit beside members (review of #1062) ----------------------------------------

WORKSPACE = {
    "pyproject.toml": "[project]\nname = 'root'\n\n[tool.uv.workspace]\nmembers = ['packages/a', 'packages/b']\n",
    "root/__init__.py": "r = 0\n",
    ".github/workflows/ci.yml": "on: [pull_request]\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: pytest --cov\n",
    "packages/a/pyproject.toml": "[project]\nname = 'a'\n\n[tool.coverage.report]\nfail_under = 90\n",
    "packages/a/a/__init__.py": "x = 1\n",
    "packages/a/CLAUDE.md": "# A\n\nRun `pytest`.\n",
    "packages/b/pyproject.toml": "[project]\nname = 'b'\n",
    "packages/b/b/__init__.py": "y = 1\n",
}


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> dict[str, ul.UnitLevel]:
    from assess_core import build_run_context
    repo = _write(tmp_path_factory.mktemp("ws") / "repo", WORKSPACE)
    ctx = build_run_context(repo_root=repo, run_date="2026-11-22", non_interactive=True)
    return {u["path"]: u for u in ctx["unit_levels"]["units"]}


def _state(unit: ul.UnitLevel, cid: str) -> str:
    return next(c["state"] for c in unit["conditions"] if c["id"] == cid)


def test_the_root_unit_does_not_inherit_a_members_coverage_gate(
        workspace: dict[str, ul.UnitLevel]) -> None:
    assert set(workspace) == {".", "packages/a", "packages/b"}
    root = workspace["."]
    assert "layer-6-coverage.1" not in root["inherited"]
    assert _origin(root, "layer-6-coverage.1") == "unit"
    assert _state(root, "layer-6-coverage.1") != "met"


def test_the_root_unit_does_not_inherit_a_members_instruction_file(
        workspace: dict[str, ul.UnitLevel]) -> None:
    root, a, b = workspace["."], workspace["packages/a"], workspace["packages/b"]
    assert _state(a, "layer-0-instructions.1") == "met"
    for unit in (root, b):
        assert _origin(unit, "layer-0-instructions.1") == "unit"
        assert _state(unit, "layer-0-instructions.1") == "unmet"
        assert unit["step"] == 1


def test_each_unit_condition_carries_the_level_blocks_why(run: dict[str, Any]) -> None:
    whys = {c["id"]: c["why"] for c in run["level"]["conditions"] if "why" in c}
    assert whys
    for unit in run["unit_levels"]["units"]:
        for c in unit["conditions"]:
            assert c.get("why") == whys.get(c["id"])
