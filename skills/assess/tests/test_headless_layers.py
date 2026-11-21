"""Headless layer 2-4 blocks: structure, stats_summary, promissory_markers, structure_drift (#667)."""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import assess_signals
from lib import headless_layers as hl
from lib import scan_registry as reg
from lib import signals
from lib import import_graph_python
from lib.counts_only import reason_code
from lib.counts_only_rules import ID, PATH, classify
from lib.levels import _UNAVAILABLE, evaluate, resolve_signal
from lib.level_signals import load_default_levels
from lib.signals_schema import SCHEMA_PATH

BLOCKS = ("structure", "stats_summary", "promissory_markers", "structure_drift")

# The signal paths the default maturity model's layer 2, 3 and 4 rows read (#663).
LAYER_SIGNALS = (
    "structure.modularity_q",
    "structure.front_door_ratio",
    "structure.cross_package_edges",
    "promissory_markers.aging_reliable",
    "promissory_markers.families.suppression.total",
    "promissory_markers.families.suppression.stale",
    "structure_drift.tier_0.boundary_patterns",
    "stats_summary.loc.p95",
)

LONG_NAME = "a_function_name_long_enough_to_fail_the_forty_character_rule"
LONG_OWNER = "@an-owner-team-name-long-enough-to-fail-the-forty-character-rule"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A Python package of two modules, a lint suppression and a CODEOWNERS map."""
    repo = tmp_path / "target"
    _write(repo, "pkg/__init__.py", "")
    _write(repo, "pkg/core.py", f"def {LONG_NAME}(x):\n    if x:\n        return 1\n    return 2\n")
    _write(repo, "pkg/api.py", "import os  # noqa: F401\nfrom pkg import core\n\n"
                               "def run():\n    return core\n")
    _write(repo, "CODEOWNERS", f"/pkg/ {LONG_OWNER}\n/missing-dir-with-a-long-planted-name/ @x\n")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture
def real_lizard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed lizard, not the empty stub test_complexity_treemap installs."""
    for name in ("lizard", "lizard_languages"):
        module = sys.modules.get(name)
        if module is not None and not hasattr(module, "analyze_file") and name == "lizard":
            monkeypatch.delitem(sys.modules, name)
    importlib.import_module("lizard")


def _run(repo: Path, out: Path, *extra: str) -> dict[str, Any]:
    assert assess_signals.main([str(repo), "--out", str(out), "--scans", ",".join(BLOCKS),
                                *extra]) == 0
    doc: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return doc


# -- the table ---------------------------------------------------------------

def test_the_signal_table_validates_and_reports_the_four_blocks() -> None:
    reg.validate(signals.SIGNAL_SCANS)
    reported = {spec.key for spec in signals.SIGNAL_SCANS if signals._reported(spec)}
    assert set(BLOCKS) <= reported
    assert not set(BLOCKS) & set(signals.UNREGISTERED_SCANS)


def test_only_core_entries_leave_the_signal_table() -> None:
    """A headless entry displaces core entries only, never a reported scan."""
    dropped = [spec for spec in reg.SCANS if spec not in signals.SIGNAL_SCANS]
    assert {spec.key for spec in dropped} >= {"promissory_markers", "structure_drift_block"}
    assert all(spec.fn.__module__ in signals.CORE_MODULES for spec in dropped)


def test_the_headless_module_is_outside_the_core() -> None:
    assert hl.__name__ not in signals.CORE_MODULES
    assert all(spec.fn.__module__ == hl.__name__ for spec in hl.HEADLESS_SCANS)


# -- each block --------------------------------------------------------------

@pytest.mark.parametrize(("reason", "code"), [
    ("grimp not installed; static structure not assessed", "no_grimp"),
    ("networkx not installed; static structure not assessed", "no_networkx"),
    ("grimp failed to build import graph (bad syntax)", "grimp_failed"),
])
def test_structure_names_a_missing_tool(reason: str, code: str) -> None:
    block = hl.structure({"available": False, "reason": reason, "modularity_q": 0.0})
    assert set(block) == {"available", "reason"}
    assert reason_code(block["reason"]) == code


def test_structure_without_a_python_package_is_unavailable_not_zero() -> None:
    block = hl.structure({"available": True, "reason": "no importable Python packages found",
                          "modularity_q": 0.0, "module_count": 0})
    assert reason_code(block["reason"]) == "no_python_packages"
    assert resolve_signal({"structure": block}, "structure.modularity_q") is _UNAVAILABLE


def test_structure_passes_a_measured_graph_through() -> None:
    scan = {"available": True, "reason": "", "modularity_q": 0.5, "module_count": 3}
    assert hl.structure(scan) is scan


def test_an_unknown_reason_is_kept_and_a_non_block_is_named() -> None:
    assert hl.promissory_markers({"available": False, "reason": "odd"}) == {
        "available": False, "reason": "odd"}
    assert reason_code(hl.promissory_markers(None)["reason"]) == "invalid_block"


def test_promissory_markers_names_missing_rg_and_a_non_git_root() -> None:
    assert reason_code(hl.promissory_markers(
        {"available": False, "reason": "rg not on PATH", "families": {}})["reason"]) == "no_rg"
    assert reason_code(hl.promissory_markers(
        {"available": False, "reason": "not a git repository root"})["reason"]) == "not_git_root"


def test_structure_drift_without_a_map_is_named(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    block = hl.structure_drift(tmp_path)
    assert reason_code(block["reason"]) == "no_ownership_map"


def test_structure_drift_reads_tier_0_and_marks_tier_1_unprobed(target: Path) -> None:
    block = hl.structure_drift(target)
    assert block["tier_0"]["matched_patterns"] == 1
    assert block["tier_0"]["total_patterns"] == 2
    assert block["tier_1"]["available"] is False
    assert reason_code(block["tier_1"]["reason"]) == "not_probed"


@pytest.mark.parametrize(("values", "q", "expected"), [
    ([], 95, 0.0), ([7], 95, 7.0), ([1, 2, 3, 4, 5], 50, 3.0), ([1, 2, 3, 4, 5], 95, 4.8),
    ([10, 20], 95, 19.5),
])
def test_percentile_matches_numpy_linear_interpolation(values: list[float], q: float,
                                                       expected: float) -> None:
    # Not pytest.approx: it calls into numpy, which test_complexity_treemap stubs.
    assert abs(hl.percentile(values, q) - expected) < 1e-9


def test_summarise_follows_the_sidecar_rules() -> None:
    block = hl.summarise([(100, [2.0, 3.0]), (10, [])])
    assert block["files_scored"] == 2
    assert block["loc"] == {"p50": 55.0, "p95": 95.5, "max": 100.0, "max_code": 100.0,
                            "max_data": 0.0, "total": 110}
    assert block["ccn"]["basis"] == "file-aggregate"
    assert block["ccn"]["max"] == 5.0 and block["ccn"]["p50"] == 3.0   # no function reads 1


def test_stats_summary_without_lizard_is_named(target: Path) -> None:
    block = hl.stats_summary(target, (set(), []), None, analyzer=lambda: None)
    assert set(block) == {"available", "reason"}
    assert reason_code(block["reason"]) == "no_lizard"


def test_stats_summary_skips_excluded_generated_and_failing_files(target: Path) -> None:
    _write(target, "pkg/schema.generated.py", "x = 1\n")
    _write(target, "vendored/lib.py", "x = 1\n")
    _git(target, "add", "-A")
    seen: list[str] = []

    def analyze(path: str) -> hl.FileScore | None:
        seen.append(Path(path).relative_to(target.resolve()).as_posix())
        if path.endswith("api.py"):
            raise ValueError("unreadable")
        return (5, [1.0]) if path.endswith(".py") else None

    block = hl.stats_summary(target, ({"vendored"}, []), None, analyzer=lambda: analyze)
    assert "pkg/schema.generated.py" not in seen and "vendored/lib.py" not in seen
    assert "pkg/api.py" in seen
    assert block["files_scored"] == 2   # __init__.py and core.py; api.py raised


def test_a_stub_lizard_reads_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "lizard", type(sys)("lizard"))
    assert hl.lizard_analyzer() is None


def test_stats_summary_scores_with_the_real_lizard(target: Path, real_lizard: None) -> None:
    block = hl.stats_summary(target, (set(), []), None)
    assert block["available"] is True
    assert block["files_scored"] == 3
    assert block["ccn"]["max"] == 2.0


# -- end to end --------------------------------------------------------------

def test_counts_only_run_gives_every_layer_signal_a_value(
    target: Path, tmp_path: Path, real_lizard: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The six signals of the layer 2-4 rows resolve; nothing long and copied survives.

    Runs from inside the target, so a cache written to the working directory
    (grimp's, lizard's) would show in ``git status``. A second package imports
    the first, so the front-door ratio has a boundary to measure (#760).
    """
    _write(target, "pkg2/__init__.py", "from pkg import api\n")
    _git(target, "add", "-A")
    _git(target, "commit", "-qm", "second package")
    monkeypatch.chdir(target)
    doc = _run(target, tmp_path / "co.json", "--counts-only")
    assert _git(target, "status", "--porcelain", "--untracked-files=all") == ""
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    for signal in LAYER_SIGNALS:
        value = resolve_signal(doc["scans"], signal)
        assert isinstance(value, (int, float)), signal
    text = json.dumps(doc)
    for planted in (LONG_NAME, LONG_OWNER, "missing-dir-with-a-long-planted-name", "F401"):
        assert planted not in text
    for pattern, value in _strings(doc["scans"]):
        assert len(value) <= 40 or classify(pattern) in (PATH, ID), (pattern, value)


def test_the_default_model_gives_layers_2_to_4_a_whole_level_from_counts_only(
    target: Path, tmp_path: Path, real_lizard: None,
) -> None:
    """The fleet path: a counts-only headless document through the default rows."""
    _write(target, "pkg2/__init__.py", "from pkg import api\n")
    _git(target, "add", "-A")
    _git(target, "commit", "-qm", "second package")
    doc = _run(target, tmp_path / "co.json", "--counts-only")
    result = evaluate(load_default_levels(),
                      {"name": "g", "settings": None, "repos": {"r": doc["scans"]}})
    levels = {row["id"]: row["level"] for row in result["rows"]}
    assert resolve_signal(doc["scans"], "structure.cross_package_edges") > 0
    for row_id in ("layer-2-design", "layer-3-linters", "layer-4-architecture"):
        assert isinstance(levels[row_id], int), (row_id, levels[row_id])
    # One suppression on two commits: a linter runs, but history too thin to age it.
    assert levels["layer-3-linters"] == 1
    assert doc["scans"]["promissory_markers"]["aging_reliable"] is False
    assert levels["layer-4-architecture"] == 2   # a matched CODEOWNERS path, small files


def test_no_tool_run_reads_unknown_with_named_reasons(
    target: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No lizard, grimp or rg: each block is unavailable with its code, never a level."""
    git_only = tmp_path / "bin"
    git_only.mkdir()
    git = shutil.which("git")
    assert git is not None
    (git_only / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(git_only))
    monkeypatch.setattr(import_graph_python, "_GRIMP_AVAILABLE", False)
    monkeypatch.setattr(hl, "lizard_analyzer", lambda: None)
    doc = _run(target, tmp_path / "co.json", "--counts-only")
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    scans = doc["scans"]
    assert {name: scans[name]["reason"] for name in ("structure", "stats_summary",
                                                     "promissory_markers")} == {
        "structure": "no_grimp", "stats_summary": "no_lizard", "promissory_markers": "no_rg"}
    for signal in LAYER_SIGNALS:
        if not signal.startswith("structure_drift"):
            assert resolve_signal(scans, signal) is _UNAVAILABLE, signal


@pytest.mark.parametrize("counts_only", [False, True])
def test_an_edgeless_graph_reads_layer_two_unknown_with_its_reason(
    tmp_path: Path, counts_only: bool,
) -> None:
    """Modules that never import each other: modularity null, named, never level 0 (#682)."""
    repo = tmp_path / "edgeless"
    for rel in ("pkg/__init__.py", "pkg/a.py", "pkg/b.py"):
        _write(repo, rel, "x = 1\n")
    _git(repo.parent, "init", "-q", str(repo))
    doc = _run(repo, tmp_path / "out.json", *(["--counts-only"] if counts_only else []))
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    block = doc["scans"]["structure"]
    assert block["available"] is True and block["edge_count"] == 0
    assert block["modularity_q"] is None
    assert block["modularity_reason"] == "no_import_edges"
    result = evaluate(load_default_levels(),
                      {"name": "g", "settings": None, "repos": {"r": doc["scans"]}})
    row = next(r for r in result["rows"] if r["id"] == "layer-2-design")
    assert (row["status"], row["level"]) == ("unknown", None)
    assert "structure.modularity_q" in row["missing_signals"]


def test_a_headless_run_reports_no_block_as_not_registered(target: Path, tmp_path: Path) -> None:
    doc = _run(target, tmp_path / "full.json")
    for name in BLOCKS:
        assert reason_code(doc["scans"][name].get("reason")) != "not_registered", name
    assert os.path.exists(tmp_path / "full.json")


def _strings(value: Any, pattern: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((pattern, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.append((f"{pattern}.<key>", key))
            found += _strings(item, f"{pattern}.{key}" if pattern else key)
    elif isinstance(value, list):
        for item in value:
            found += _strings(item, pattern)
    return found
