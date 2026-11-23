"""Per-unit readings of the scope-sensitive scans (lib/unit_readings.py, issue #863).

Scoped scans run again per unit; override blocks are the unit's own only with its own
evidence; every other block is inherited. Each condition carries its origin.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib import check_manifest as cm
from lib import core_scans as cs
from lib import run_setup as rs
from lib import unit_readings as ur
from lib.counts_only import counts_only
from lib.instruction_files import nested_instruction_surface
from lib.layer_grades import scan_layer_grades
from lib.levels import resolve_signal
from lib.scan_registry import SCANS, STAGE_READ_SIDE, ScanSpec
from lib.signals import HEADLESS_SCANS
from lib.unit_discovery import scan_units

NO_EXCLUDES: tuple[set[str], list[str]] = (set(), [])

MONOREPO = {
    "CLAUDE.md": "# Repo\n\nRun `make check`.\n",
    ".github/workflows/ci.yml": "on: [pull_request]\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: pytest\n",
    "services/billing/pyproject.toml": "[project]\nname = 'billing'\n\n[tool.coverage.report]\nfail_under = 90\n",
    "services/billing/billing/__init__.py": "x = 1  # noqa: E501\n",
    "services/billing/CLAUDE.md": "# Billing\n\nRun `pytest`.\n",
    "services/search/pyproject.toml": "[project]\nname = 'search'\n",
    "services/search/search/__init__.py": "y = 2\n",
    "web/package.json": '{"name": "web", "main": "src/index.js"}\n',
    "web/src/index.js": "module.exports = 1\n",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                   encoding="utf-8", errors="surrogateescape", check=True)


def _write(root: Path, files: dict[str, str]) -> Path:
    """``files`` committed in a fresh git repository at ``root``."""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def _working_notes(root: Path) -> Any:
    return cs.working_notes(root)


def _read(root: Path) -> ur.UnitReadingsBlock:
    units = scan_units(root, NO_EXCLUDES)
    nested = nested_instruction_surface(root, NO_EXCLUDES)
    return ur.read_units(units, nested, NO_EXCLUDES, root, _working_notes(root))


def _origins(reading: ur.UnitReading) -> dict[str, str]:
    return {c["id"]: c["origin"] for c in reading["conditions"]}


@pytest.fixture(scope="module")
def monorepo(tmp_path_factory: pytest.TempPathFactory) -> ur.UnitReadingsBlock:
    return _read(_write(tmp_path_factory.mktemp("mono"), MONOREPO))


# --- the split ------------------------------------------------------------------

def test_the_unit_blocks_are_the_scoped_and_override_model_blocks() -> None:
    assert ur.unit_blocks(cm.core_manifest()) == [
        "coverage_gate", "doc_staleness", "layer_grades", "promissory_markers",
        "stats_summary", "structure"]


def test_scope_classes_come_from_the_manifest() -> None:
    scopes = ur.unit_scopes(cm.core_manifest())
    assert {b for b, s in scopes.items() if s == "override"} == set(ur.OVERRIDES)
    assert scopes["doc_graph"] == scopes["doc_staleness"] == "scoped"
    assert "ci_checks" not in scopes  # repo-wide: unit-local CI is not read yet


def test_the_unit_pass_reruns_only_scope_sensitive_scans() -> None:
    table, external = ur.unit_table(cm.core_manifest(), SCANS, HEADLESS_SCANS)
    keys = [s.key for s in table]
    assert keys == ["promissory", "doc_graph", "doc_staleness", "structure_scan",
                    "promissory_markers", "coverage_gate", "stats_summary", "structure"]
    assert all("scope" in s.reads or set(s.reads) & set(keys) for s in table)
    assert external == ur.UNIT_EXTERNAL_READS


def test_the_registry_binds_the_core_unit_table() -> None:
    table, external = ur.bound_table()
    assert [s.key for s in table] == [s.key for s in ur.unit_table(cm.core_manifest(), SCANS, HEADLESS_SCANS)[0]]
    assert external == ur.UNIT_EXTERNAL_READS


def test_the_repo_blocks_are_the_unit_blocks() -> None:
    assert list(ur.REPO_BLOCKS) == ur.unit_blocks(cm.core_manifest())


def test_the_root_unit_reads_the_repo_blocks_without_a_pass(tmp_path: Path) -> None:
    def never(*_: Any) -> None:
        raise AssertionError("the root unit ran the unit pass")

    repo_blocks = {"stats_summary": {"available": True, "loc": {"p95": 40.0}, "files_scored": 3},
                   "coverage_gate": {"available": True, "gates": [{"file": "pyproject.toml"}],
                                     "enforced_meets_minimum": True}}
    block = cs.unit_readings({"unit_count": 1, "units": [{"path": "."}]}, None,
                             *[repo_blocks.get(b) for b in ur.REPO_BLOCKS],
                             NO_EXCLUDES, tmp_path, None)
    own = block["units"][0]["blocks"]
    assert own["stats_summary"] == {"available": True, "loc": {"p95": 40.0}, "files_scored": 3}
    assert own["coverage_gate"]["enforced_meets_minimum"] is True
    assert ur.read_units({"units": [{"path": "."}]}, None, NO_EXCLUDES, tmp_path, None,
                         run=never, repo_blocks={})["units"][0]["path"] == "."


def test_a_root_only_instruction_file_is_the_root_units_own(tmp_path: Path) -> None:
    # nested_instructions never lists the root CLAUDE.md; layer_grades carries it, and a
    # member unit's nested file is not credited to the root.
    layer_grades = {"available": True, "instruction_file_present": True,
                    "instruction_presence": "root"}
    nested = {"available": True, "files": [{"path": "svc/a/CLAUDE.md"}]}
    blocks = {"layer_grades": layer_grades}
    block = cs.unit_readings(
        {"unit_count": 2, "units": [{"path": "."}, {"path": "svc/b"}]}, nested,
        *[blocks.get(b) for b in ur.REPO_BLOCKS], NO_EXCLUDES, tmp_path, None)
    root, member = block["units"]
    assert root["blocks"]["layer_grades"] == {"available": True, "instruction_file_present": True}
    assert _origins(root)["layer-0-instructions.1"] == "unit"
    assert "layer_grades" not in member["blocks"]
    assert _origins(member)["layer-0-instructions.1"] == "inherited"


def test_the_root_unit_is_not_credited_with_a_members_nested_file(tmp_path: Path) -> None:
    repo = _write(tmp_path / "repo", {
        "pyproject.toml": "[project]\nname = 'root'\n",
        "root_pkg/__init__.py": "x = 1\n",
        "svc/a/pyproject.toml": "[project]\nname = 'a'\n",
        "svc/a/a/__init__.py": "y = 1\n",
        "svc/a/CLAUDE.md": "# A\n\nRun `pytest`.\n",
    })
    nested = nested_instruction_surface(repo, NO_EXCLUDES)
    grades = scan_layer_grades(rs.instruction_grading(repo), nested)
    assert grades["instruction_presence"] == "nested_only"
    blocks = {"layer_grades": grades}
    units = {"unit_count": 2, "units": [{"path": "."}, {"path": "svc/a"}]}
    block = cs.unit_readings(units, nested, *[blocks.get(b) for b in ur.REPO_BLOCKS],
                             NO_EXCLUDES, repo, None)
    root, member = block["units"]
    # #864: the member's file is not the root's, so the root reads none of its own
    assert root["blocks"]["layer_grades"] == {"available": True, "instruction_file_present": False}
    assert _origins(root)["layer-0-instructions.1"] == "unit"
    assert _origins(member)["layer-0-instructions.1"] == "unit"


def test_a_root_nested_file_outside_every_member_is_the_roots() -> None:
    grades = {"available": True, "instruction_file_present": True,
              "instruction_presence": "nested_only"}
    nested = {"available": True, "files": [{"path": "svc/a/CLAUDE.md"}, {"path": "tools/AGENTS.md"}]}
    assert ur._root_without("layer_grades", grades, ["svc/a"], nested) == {
        "available": True, "instruction_file_present": True}
    assert ur._root_without("layer_grades", {**grades, "instruction_presence": "none"},
                            ["svc/a"], nested) is None


def test_the_root_unit_does_not_own_a_members_coverage_gate(tmp_path: Path) -> None:
    member_gate = {"available": True, "gates": [{"file": "svc/a/pyproject.toml"}],
                   "enforced_meets_minimum": True}
    root_gate = {**member_gate, "gates": [{"file": "pyproject.toml"}]}
    units = {"unit_count": 2, "units": [{"path": "."}, {"path": "svc/a"}]}
    for gate, owned in ((member_gate, False), (root_gate, True)):
        blocks = {"coverage_gate": gate}
        block = cs.unit_readings(units, None, *[blocks.get(b) for b in ur.REPO_BLOCKS],
                                 NO_EXCLUDES, tmp_path, None)
        root = block["units"][0]
        # #864: without a gate of its own the root reads a no-gate reading, never the member's
        assert root["blocks"]["coverage_gate"]["enforced_meets_minimum"] is owned
        assert _origins(root)["layer-6-coverage.1"] == "unit"
    unmeasured = {**member_gate, "unmeasured_reason": "go_rust_gate_form_not_read",
                  "enforced_meets_minimum": None}
    assert ur._root_without("coverage_gate", unmeasured, ["svc/a"], None)[
        "enforced_meets_minimum"] is None
    no_gate = ur._root_without("coverage_gate", {**member_gate, "enforced_basis": "ci_run"},
                               ["svc/a"], None)
    assert no_gate is not None and no_gate["enforced_basis"] == "no_gate"
    assert ur._root_without("coverage_gate", {**member_gate, "gates": []}, ["svc/a"], None) is None


def test_a_parent_build_gate_is_inherited_beside_a_members_gate(tmp_path: Path) -> None:
    repo_gate = {"available": True, "enforced_meets_minimum": True,
                 "gates": [{"file": "svc/a/pyproject.toml"}, {"file": "pom.xml"}]}
    no_gate = {"available": True, "gates": [], "enforced_meets_minimum": False}
    assert ur._coverage_own("svc/b", no_gate, {"coverage_gate": repo_gate,
                                               "members": ["svc/a", "svc/b"]}) is None
    only_a = {**repo_gate, "gates": [{"file": "svc/a/pyproject.toml"}]}
    assert ur._coverage_own("svc/b", no_gate, {"coverage_gate": only_a,
                                               "members": ["svc/a", "svc/b"]}) == no_gate
    # no gate anywhere: the repository's reading (configured false) is inherited
    assert ur._coverage_own("svc/b", no_gate, {"coverage_gate": no_gate,
                                               "members": ["svc/a", "svc/b"]}) is None


def test_a_root_no_gate_reading_beside_go_is_not_measured(tmp_path: Path) -> None:
    # a member gate that meets the minimum leaves the repository reason null; the root's
    # own no-gate reading beside a go.mod is still unknown, never unmet
    repo = _write(tmp_path / "repo", {"go.mod": "module x\n", "svc/a/pyproject.toml": "x\n"})
    meets = {"available": True, "gates": [{"file": "svc/a/pyproject.toml"}],
             "enforced_meets_minimum": True, "unmeasured_reason": None}
    out = ur._root_without("coverage_gate", meets, ["svc/a"], None,
                           {"repo_root": repo, "excludes": NO_EXCLUDES})
    assert out is not None
    assert out["enforced_meets_minimum"] is None
    assert out["unmeasured_reason"] == "go_rust_gate_form_not_read"
    plain = _write(tmp_path / "plain", {"svc/a/pyproject.toml": "x\n"})
    out = ur._root_without("coverage_gate", meets, ["svc/a"], None,
                           {"repo_root": plain, "excludes": NO_EXCLUDES})
    assert out is not None and out["enforced_meets_minimum"] is False


def _nested(*entries: tuple[str, str]) -> dict[str, Any]:
    return {"available": True, "files": [{"path": p, "scope": {"directory": d, "globs": [],
                                                                "always_loaded": False}}
                                         for p, d in entries]}


def test_a_sibling_units_nested_file_is_not_inherited() -> None:
    nested_only = {"available": True, "instruction_file_present": True,
                   "instruction_presence": "nested_only"}
    sibling = _nested(("svc/a/AGENTS.md", "svc/a"))
    ctx = {"layer_grades": nested_only, "nested_instructions": sibling}
    assert ur._instructions_own("svc/b", None, ctx) == {
        "available": True, "instruction_file_present": False}
    ancestor = _nested(("svc/AGENTS.md", "svc"))
    assert ur._instructions_own("svc/b", None, {**ctx, "nested_instructions": ancestor}) is None
    root = {**nested_only, "instruction_presence": "root"}
    assert ur._instructions_own("svc/b", None, {**ctx, "layer_grades": root}) is None
    # not measured stays inherited (the repository's null), never a false own reading
    unmeasured = {"available": True, "instruction_file_present": None, "instruction_presence": None}
    assert ur._instructions_own("svc/b", None, {**ctx, "layer_grades": unmeasured}) is None


def test_a_path_scoped_rule_covers_only_the_units_its_globs_reach() -> None:
    nested_only = {"available": True, "instruction_file_present": True,
                   "instruction_presence": "nested_only"}
    rule = {"available": True, "files": [{"path": ".claude/rules/a.md", "scope": {
        "directory": ".", "globs": ["packages/a/**"], "always_loaded": False}}]}
    ctx = {"layer_grades": nested_only, "nested_instructions": rule}
    assert ur._instructions_own("packages/a", None, ctx) is None  # inherits the rule
    assert ur._instructions_own("packages/b", None, ctx) == {
        "available": True, "instruction_file_present": False}
    assert ur._glob_reaches("**/*.py", "packages/b") is True
    assert ur._glob_reaches("packages/*/src/**", "packages/b") is True
    assert ur._glob_reaches("packages/a/src/x.py", "packages/a") is True
    assert ur._glob_reaches("docs/**", "packages/a") is False


def test_a_nested_rules_glob_reads_relative_to_its_folder_too() -> None:
    nested = {"path": "svc/.claude/rules/x.md",
              "scope": {"directory": "svc", "globs": ["b/**"], "always_loaded": False}}
    assert ur._covers(nested, "svc/b") is True
    assert ur._covers(nested, "svc/c") is False
    # the rule's folder-relative reading reaches only the member svc/b: not the root's
    assert ur._root_file(nested, ["svc/b"]) is False


def test_a_root_rule_scoped_to_a_member_is_not_the_roots() -> None:
    grades = {"available": True, "instruction_file_present": True,
              "instruction_presence": "nested_only"}

    def rule(*globs: str) -> dict[str, Any]:
        return {"available": True, "files": [{"path": ".claude/rules/a.md", "scope": {
            "directory": ".", "globs": list(globs), "always_loaded": False}}]}

    members = ["packages/a", "packages/b"]
    assert ur._root_without("layer_grades", grades, members, rule("packages/a/**")) == {
        "available": True, "instruction_file_present": False}
    for reaching in (rule("tools/**"), rule("**/*.py"), rule("packages/a/**", "scripts/*.sh")):
        assert ur._root_without("layer_grades", grades, members, reaching) == {
            "available": True, "instruction_file_present": True}


def test_a_sibling_nested_file_through_the_unit_pass(tmp_path: Path) -> None:
    repo = _write(tmp_path / "repo", {
        "svc/a/pyproject.toml": "[project]\nname = 'a'\n",
        "svc/a/a/__init__.py": "x = 1\n",
        "svc/a/CLAUDE.md": "# A\n\nRun `pytest`.\n",
        "svc/b/pyproject.toml": "[project]\nname = 'b'\n",
        "svc/b/b/__init__.py": "y = 1\n",
    })
    nested = nested_instruction_surface(repo, NO_EXCLUDES)
    grades = scan_layer_grades(rs.instruction_grading(repo), nested)
    assert grades["instruction_presence"] == "nested_only"
    blocks = {"layer_grades": grades}
    units = {"unit_count": 2, "units": [{"path": "svc/a"}, {"path": "svc/b"}]}
    block = cs.unit_readings(units, nested, *[blocks.get(b) for b in ur.REPO_BLOCKS],
                             NO_EXCLUDES, repo, None)
    a, b = block["units"]
    assert resolve_signal(a["blocks"], "layer_grades.instruction_file_present") is True
    assert resolve_signal(b["blocks"], "layer_grades.instruction_file_present") is False
    assert _origins(b)["layer-0-instructions.1"] == "unit"


def test_no_model_condition_mixes_unit_and_repo_wide_blocks() -> None:
    # An origin is "unit" when any criterion reads an own block (#864 relies on a level
    # reading one kind only); a level mixing both would need a finer rule.
    from lib.level_tally import default_model
    own = set(ur.unit_blocks(cm.core_manifest()))
    for row in default_model()["rows"]:
        for level in row["levels"]:
            reads = {c["signal"].split(".")[0] for c in level["criteria"]}
            assert reads <= own or not reads & own, (row["id"], level["level"], sorted(reads))


def test_the_registry_entry_reads_what_the_unit_pass_needs() -> None:
    spec = next(s for s in SCANS if s.key == "unit_readings")
    assert spec.reads == (*ur.OWN_READS, *ur.UNIT_EXTERNAL_READS)
    assert spec.fn is cs.unit_readings


def test_a_scoped_block_whose_producer_ignores_the_scope_is_rejected() -> None:
    manifest = cm.core_manifest()
    blind = [s if s.key != "coverage_gate" else
             ScanSpec("coverage_gate", lambda root: {}, ("repo_root",), STAGE_READ_SIDE)
             for s in SCANS]
    with pytest.raises(ValueError, match="does not read the scope"):
        ur.unit_table(manifest, blind, HEADLESS_SCANS)


def test_a_unit_block_with_no_plain_producer_or_variant_is_rejected() -> None:
    with pytest.raises(ValueError, match="no plain core scan"):
        ur.unit_table(cm.core_manifest(), SCANS, ())


def test_the_unit_pass_reads_only_the_values_it_is_passed() -> None:
    with pytest.raises(ValueError, match="does not pass"):
        ur.read_units({"units": []}, None)


# --- the #800 fixture -------------------------------------------------------------

def test_three_units_each_get_their_own_readings(monorepo: ur.UnitReadingsBlock) -> None:
    assert monorepo["available"] is True
    assert monorepo["unit_count"] == 3
    assert [u["path"] for u in monorepo["units"]] == ["services/billing", "services/search", "web"]
    for unit in monorepo["units"]:
        assert {"doc_staleness", "promissory_markers", "stats_summary", "structure"} <= set(unit["blocks"])


def test_a_unit_instruction_file_overrides_the_root(monorepo: ur.UnitReadingsBlock) -> None:
    billing, search, web = monorepo["units"]
    assert resolve_signal(billing["blocks"], "layer_grades.instruction_file_present") is True
    assert _origins(billing)["layer-0-instructions.1"] == "unit"
    for other in (search, web):
        assert "layer_grades" not in other["blocks"]
        assert _origins(other)["layer-0-instructions.1"] == "inherited"


def test_a_unit_coverage_gate_overrides_the_parent_build(monorepo: ur.UnitReadingsBlock) -> None:
    billing, search, _ = monorepo["units"]
    assert billing["blocks"]["coverage_gate"]["available"] is True
    assert "enforced_meets_minimum" in billing["blocks"]["coverage_gate"]
    assert _origins(billing)["layer-6-coverage.1"] == "unit"
    # Without the repository-wide readings there is no parent gate to weigh, so search
    # inherits; a full run, which passes them, reads search's own (tests/test_unit_levels.py)
    assert "coverage_gate" not in search["blocks"]
    assert _origins(search)["layer-6-coverage.1"] == "inherited"


def test_repo_wide_conditions_are_inherited_and_scoped_ones_are_the_units(
        monorepo: ur.UnitReadingsBlock) -> None:
    for unit in monorepo["units"]:
        origins = _origins(unit)
        assert origins["layer-5-ci.1"] == "inherited"
        assert origins["layer-7-review.1"] == "inherited"
        assert origins["layer-8-agent-ops.1"] == "inherited"
        assert origins["layer-3-linters.2"] == "unit"
        assert origins["doc-freshness.1"] == "unit"
        assert origins["layer-4-architecture.2"] == "unit"


def test_every_model_condition_is_tagged(monorepo: ur.UnitReadingsBlock) -> None:
    from lib.level_tally import condition_ids, default_model
    for unit in monorepo["units"]:
        assert {c["id"] for c in unit["conditions"]} == set(condition_ids(default_model()))
        assert {c["origin"] for c in unit["conditions"]} <= {"unit", "inherited"}


def test_a_scoped_reading_sees_only_its_unit(monorepo: ur.UnitReadingsBlock) -> None:
    billing, search, web = monorepo["units"]
    for unit in (billing, search, web):
        assert unit["blocks"]["promissory_markers"].get("available") is not False
    # billing's one suppression marker is billing's alone
    assert billing["blocks"]["promissory_markers"] != search["blocks"]["promissory_markers"]
    assert search["blocks"]["promissory_markers"] == web["blocks"]["promissory_markers"]


def test_a_full_core_run_carries_the_unit_readings(tmp_path: Path) -> None:
    from assess_core import build_run_context
    repo = _write(tmp_path / "repo", MONOREPO)
    ctx = build_run_context(repo_root=repo, run_date="2026-11-22", non_interactive=True)
    block = ctx["unit_readings"]
    assert block["available"] is True
    assert [u["path"] for u in block["units"]] == ["services/billing", "services/search", "web"]
    assert _origins(block["units"][0])["layer-0-instructions.1"] == "unit"
    assert list(ctx).index("unit_readings") > list(ctx).index("units")


# --- unknown stays unknown ---------------------------------------------------------

def test_a_null_unit_count_stays_null(tmp_path: Path) -> None:
    units = {"unit_count": None, "unit_count_reason": "unreadable_manifest", "units": []}
    block = ur.read_units(units, None, NO_EXCLUDES, tmp_path, _working_notes(tmp_path),
                          table=((), ur.UNIT_EXTERNAL_READS))
    assert block["available"] is True
    assert block["unit_count"] is None
    assert block["unit_count_reason"] == "unreadable_manifest"


def test_a_degraded_units_block_reads_unavailable(tmp_path: Path) -> None:
    block = ur.read_units({"available": False, "reason": "boom"}, None, NO_EXCLUDES, tmp_path,
                          _working_notes(tmp_path), table=((), ur.UNIT_EXTERNAL_READS))
    assert block["available"] is False
    assert block["unit_count"] is None
    assert block["units"] == []


def test_a_failing_scan_degrades_for_that_unit_only(tmp_path: Path) -> None:
    def boom(root: Any, excludes: Any, scope: Any) -> Any:
        raise RuntimeError("scan broke")

    table = (ScanSpec("promissory_markers", boom, ("repo_root", "excludes", "scope"),
                      STAGE_READ_SIDE),)
    units = {"unit_count": 1, "units": [{"path": "a"}]}
    block = ur.read_units(units, None, NO_EXCLUDES, tmp_path, None,
                          table=(table, ur.UNIT_EXTERNAL_READS))
    own = block["units"][0]["blocks"]["promissory_markers"]
    assert own["available"] is False
    assert resolve_signal(block["units"][0]["blocks"], "promissory_markers.aging_reliable") \
        is not True


def test_a_failing_unit_coverage_scan_is_the_units_unknown_not_the_parents_gate(
        tmp_path: Path) -> None:
    def boom(root: Any, scope: Any, excludes: Any) -> Any:
        raise RuntimeError("gate scan broke")

    table = (ScanSpec("coverage_gate", boom, ("repo_root", "scope", "excludes"),
                      STAGE_READ_SIDE),)
    units = {"unit_count": 1, "units": [{"path": "a"}]}
    block = ur.read_units(units, None, NO_EXCLUDES, tmp_path, None,
                          table=(table, ur.UNIT_EXTERNAL_READS))
    unit = block["units"][0]
    assert unit["blocks"]["coverage_gate"]["available"] is False
    assert _origins(unit)["layer-6-coverage.1"] == "unit"
    assert resolve_signal(unit["blocks"], "coverage_gate.enforced_meets_minimum") is not True


# --- the projection ---------------------------------------------------------------

def test_projection_keeps_the_model_paths_and_their_availability() -> None:
    block = {"available": True, "loc": {"p95": 120.0, "max": 900.0}, "files_scored": 4,
             "top_hotspots": [{"path": "secret.py"}]}
    out = ur.project(block, ["loc.p95", "files_scored"])
    assert out == {"available": True, "loc": {"p95": 120.0}, "files_scored": 4}


def test_projection_keeps_an_unavailable_reason() -> None:
    out = ur.project({"available": False, "reason": "no_lizard: lizard not installed"}, ["loc.p95"])
    assert out == {"available": False, "reason": "no_lizard: lizard not installed"}
    assert ur.project(None, ["x"])["available"] is False


def test_projection_keeps_a_nested_unavailable_marker() -> None:
    block = {"available": True, "families": {"available": False, "reason": "no_rg: x"}}
    out = ur.project(block, ["families.suppression.stale"])
    assert out["families"] == {"available": False, "reason": "no_rg: x"}


def test_the_root_unit_holds_every_path() -> None:
    assert ur._under("a/b.py", ".") is True
    assert ur._under("a/b.py", "a") is True
    assert ur._under("ab/c.py", "a") is False
    assert ur._under(None, "a") is False


# --- counts-only ----------------------------------------------------------------------

def test_counts_only_drops_unit_paths_and_keeps_origins(monorepo: ur.UnitReadingsBlock) -> None:
    out = counts_only({"unit_readings": monorepo})["unit_readings"]
    text = json.dumps(out)
    assert "services/billing" not in text and "web" not in [u.get("path") for u in out["units"]]
    assert out["unit_count"] == 3
    assert out["unit_blocks"] == monorepo["unit_blocks"]
    first = out["units"][0]
    assert {c["origin"] for c in first["conditions"]} == {"unit", "inherited"}
    assert all(c["id"] for c in first["conditions"])


def _without_reasons(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _without_reasons(v) for k, v in node.items() if k != "reason"}
    return node


def test_counts_only_keeps_every_projected_reading(monorepo: ur.UnitReadingsBlock) -> None:
    out = counts_only({"unit_readings": monorepo})["unit_readings"]
    for kept, unit in zip(out["units"], monorepo["units"]):
        assert kept["blocks"] == _without_reasons(unit["blocks"])
    billing = out["units"][0]["blocks"]
    assert billing["promissory_markers"]["families"]["suppression"]["stale"] == 0
