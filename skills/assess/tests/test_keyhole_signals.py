"""Unit tests for the deterministic keyhole-signal integration helpers.

These cover the *derivation* logic that turns the five lib modules' outputs
into the run-context blocks and the six named derived findings - the pure,
git-free core of task #5. End-to-end wiring (build_run_context) is covered in
test_assess_core.py.
"""
from __future__ import annotations

from pathlib import Path

from lib import keyhole_signals as ks


# --- containment_by_dir ------------------------------------------------------

def test_containment_by_dir_flags_island_and_bleeder() -> None:
    """A directory whose commits stay inside it scores high; one whose commits
    keep dragging in outside files scores low."""
    commit_sets = [
        # island/ changes alone, repeatedly (self-contained)
        {Path("island/a.py")},
        {Path("island/a.py"), Path("island/b.py")},
        {Path("island/b.py")},
        {Path("island/a.py")},
        {Path("island/c.py")},
        # bleeder/ always drags in core/
        {Path("bleeder/x.py"), Path("core/util.py")},
        {Path("bleeder/y.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("core/other.py")},
        {Path("bleeder/z.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("shared/s.py")},
    ]
    cont = ks.containment_by_dir(Path("/nonexistent"), commit_sets, min_commits=5)
    assert cont["island"] == 1.0
    assert cont["bleeder"] == 0.0
    # The repo root "." is never a candidate directory (vacuously contained).
    assert "." not in cont


def test_containment_by_dir_respects_min_commits() -> None:
    """Directories touched fewer than min_commits times are omitted."""
    commit_sets = [{Path("rare/a.py")}, {Path("rare/a.py")}]
    cont = ks.containment_by_dir(Path("/nonexistent"), commit_sets, min_commits=5)
    assert cont == {}


# --- static-modularity projection -------------------------------------------

def test_project_static_modularity_repo_level_onto_dirs() -> None:
    structure = {"available": True, "modularity_q": 0.5, "front_door_ratio": 0.9}
    proj = ks.project_static_modularity(structure, ["a", "b"])
    assert proj == {
        "a": {"modularity_q": 0.5, "front_door_ratio": 0.9},
        "b": {"modularity_q": 0.5, "front_door_ratio": 0.9},
    }


def test_project_static_modularity_none_when_unavailable() -> None:
    assert ks.project_static_modularity({"available": False}, ["a"]) is None
    assert ks.project_static_modularity(None, ["a"]) is None


# --- behaviour block ---------------------------------------------------------

def test_behaviour_block_hidden_coupling_when_modular_but_bleeds() -> None:
    """A bleeding dir that looks modular statically becomes hidden_coupling."""
    commit_sets = [
        {Path("looksmodular/x.py"), Path("core/util.py")},
        {Path("looksmodular/y.py"), Path("core/util.py")},
        {Path("looksmodular/x.py"), Path("core/other.py")},
        {Path("looksmodular/z.py"), Path("core/util.py")},
        {Path("looksmodular/x.py"), Path("shared/s.py")},
    ]
    structure = {"available": True, "modularity_q": 0.6, "front_door_ratio": 0.95}
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, structure)
    assert block["available"] is True
    hc_paths = [h["path"] for h in block["hidden_coupling_findings"]]
    assert "looksmodular" in hc_paths
    # B1 change-coupling pairs are wired in (a list; exact contents depend on
    # min_support, exercised in test_change_coupling.py).
    assert isinstance(block["change_coupling_pairs"], list)


def test_behaviour_block_bleeding_module_without_static_graph() -> None:
    """No static graph -> a bleeding dir degrades to bleeding_module."""
    commit_sets = [
        {Path("bleeder/x.py"), Path("core/util.py")},
        {Path("bleeder/y.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("core/other.py")},
        {Path("bleeder/z.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("shared/s.py")},
    ]
    block = ks.build_behaviour_block(
        Path("/nonexistent"), commit_sets, {"available": False}
    )
    findings = {f["finding"] for f in block["static_history_disagreement"]}
    assert "bleeding_module" in findings
    assert block["hidden_coupling_findings"] == []


def test_behaviour_block_non_python_dir_never_hidden_coupling() -> None:
    """The static import graph is silent on doc/config trees, so a bleeding
    non-Python dir degrades to bleeding_module, never a false hidden_coupling -
    even when a Python static graph is available."""
    commit_sets = [
        {Path("docs/a.md"), Path("core/util.py")},
        {Path("docs/b.md"), Path("core/util.py")},
        {Path("docs/a.md"), Path("core/other.py")},
        {Path("docs/c.md"), Path("core/util.py")},
        {Path("docs/a.md"), Path("src/s.py")},
    ]
    structure = {"available": True, "modularity_q": 0.6, "front_door_ratio": 0.95}
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, structure)
    hc_paths = [h["path"] for h in block["hidden_coupling_findings"]]
    assert "docs" not in hc_paths
    findings = {f["path"]: f["finding"] for f in block["static_history_disagreement"]}
    assert findings.get("docs") == "bleeding_module"


def test_behaviour_block_refactor_boundary_is_positive() -> None:
    commit_sets = [
        {Path("island/a.py")},
        {Path("island/b.py")},
        {Path("island/a.py")},
        {Path("island/c.py")},
        {Path("island/b.py")},
    ]
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, None)
    assert any(b["path"] == "island" for b in block["refactor_boundaries"])


# --- documentation block -----------------------------------------------------

def test_documentation_block_maps_doc_join() -> None:
    doc_join = {
        "available": True,
        "high_ccn_threshold": 10.0,
        "docs": [
            {"path": "docs/api.md", "complexity_summarised": 20.0, "freshness": -1.0,
             "doc_value": -20.0, "finding": "lying_map", "confidence": "high",
             "subject_code_count": 2, "recommendation": "fix or delete"},
            {"path": "src/hot.py", "complexity_summarised": 25.0, "freshness": 0.0,
             "doc_value": 0.0, "finding": "unexplained_complexity", "confidence": None,
             "subject_code_count": 0, "recommendation": "write contract"},
        ],
        "findings": {
            "lying_maps": [{"path": "docs/api.md"}],
            "unexplained_complexity": [{"path": "src/hot.py"}],
            "good_contracts": [],
        },
    }
    block = ks.build_documentation_block(doc_join)
    assert block["available"] is True
    assert block["freshness_by_doc"] == {"docs/api.md": -1.0}
    assert "docs/api.md" in block["complexity_coverage"]
    assert "src/hot.py" not in block["freshness_by_doc"]  # not a real doc
    assert [d["path"] for d in block["stale_doc_on_complexity"]] == ["docs/api.md"]
    assert [d["path"] for d in block["unexplained_complexity"]] == ["src/hot.py"]


def test_documentation_block_unavailable_passthrough() -> None:
    block = ks.build_documentation_block({"available": False})
    assert block["available"] is False


# --- understanding block -----------------------------------------------------

def test_understanding_block_maps_understanding() -> None:
    understanding = {
        "available": True,
        "high_ccn_threshold": 10.0,
        "modules": [
            {"path": "a.py", "human_anchor": True, "intent_source": False,
             "authorship_class": "human", "days_since_comprehension_event": 3,
             "finding": None, "recommendation": None},
            {"path": "b.py", "human_anchor": False, "intent_source": False,
             "authorship_class": "agent", "days_since_comprehension_event": None,
             "finding": "orphaned_understanding", "recommendation": "anchor"},
        ],
        "orphaned_understanding": ["b.py"],
    }
    block = ks.build_understanding_block(understanding)
    assert block["human_anchor_by_path"] == {"a.py": True, "b.py": False}
    assert block["intent_source_by_path"] == {"a.py": False, "b.py": False}
    assert block["authorship_class_by_path"] == {"a.py": "human", "b.py": "agent"}
    assert block["orphaned_understanding"] == ["b.py"]


# --- runtime block -----------------------------------------------------------

def test_runtime_block_carries_static_reachability() -> None:
    dead_code = {
        "available": True, "candidate_count": 1,
        "candidates": [{"path": "dead.py", "symbol": "f", "line": 1, "kind": "unused"}],
        "tools": [{"tool": "vulture", "status": "ran"}],
        "caveat": "static only",
    }
    observability = {"rung": 1, "reachable": {"present": False, "signals": []}}
    block = ks.build_runtime_block(dead_code, observability)
    assert block["static_reachability"]["candidate_count"] == 1
    assert block["static_reachability"]["candidates"][0]["path"] == "dead.py"
    assert block["observability_rung"] == 1
    assert block["runtime_evidence_available"] is False


# --- derived findings --------------------------------------------------------

def test_assemble_findings_fixed_order_and_actions() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": ["dir/a"],
        "lying_map": ["docs/x.md"],
        "unexplained_complexity": ["src/c.py"],
        "orphaned_understanding": ["src/o.py"],
        "candidate_dead_weight": ["src/d.py"],
        "refactor_boundary": ["island"],
    })
    names = [f["name"] for f in findings]
    assert names == ks.FINDING_ORDER
    for f in findings:
        assert set(f) == {"name", "paths", "action"}
        assert isinstance(f["paths"], list)
        assert f["action"] == ks.FINDING_ACTIONS[f["name"]]
    # spot-check the exact action strings the task contract requires
    by_name = {f["name"]: f for f in findings}
    assert by_name["hidden_coupling"]["action"] == "investigate the seam"
    assert by_name["unexplained_complexity"]["action"] == (
        "write the missing contract (do NOT auto-generate)"
    )
    assert by_name["refactor_boundary"]["action"] == "safe to hand an agent in isolation"


def test_assemble_findings_dedupes_and_sorts_paths() -> None:
    findings = ks.assemble_findings({"lying_map": ["b.md", "a.md", "b.md"]})
    lying = next(f for f in findings if f["name"] == "lying_map")
    assert lying["paths"] == ["a.md", "b.md"]


def test_candidate_dead_weight_requires_positive_dead_code_evidence() -> None:
    """Bias-to-keep: a high-complexity path is dead weight ONLY when static
    reachability positively flags it AND no intent source explains it."""
    complexity_stats = {
        "ccn": {"p95": 8.0},
        "top_complex": [
            {"path": "dead.py", "ccn": 30},
            {"path": "documented.py", "ccn": 30},
            {"path": "alive.py", "ccn": 30},
        ],
    }
    dead_code = {"candidates": [
        {"path": "dead.py", "symbol": "f"},
        {"path": "documented.py", "symbol": "g"},
    ]}
    intent_source_by_path = {"documented.py": True}
    paths = ks.candidate_dead_weight_paths(
        complexity_stats, dead_code, intent_source_by_path
    )
    assert paths == ["dead.py"]  # documented.py has intent; alive.py not flagged


def test_candidate_dead_weight_empty_without_dead_code() -> None:
    """No static-reachability evidence -> no dead-weight finding (keep bias)."""
    complexity_stats = {"ccn": {"p95": 8.0}, "top_complex": [{"path": "x.py", "ccn": 30}]}
    assert ks.candidate_dead_weight_paths(complexity_stats, {"candidates": []}, {}) == []


# --- attention list ----------------------------------------------------------

def test_attention_list_ranks_by_cross_axis_count() -> None:
    findings = [
        {"name": "hidden_coupling", "paths": ["worst"], "action": "x"},
        {"name": "lying_map", "paths": ["worst"], "action": "x"},
        {"name": "unexplained_complexity", "paths": ["worst", "single"], "action": "x"},
        {"name": "refactor_boundary", "paths": ["safe"], "action": "x"},
    ]
    attention = ks.build_attention_list(findings)
    assert attention[0]["path"] == "worst"
    assert attention[0]["score"] == 3
    # the positive refactor_boundary is never an attention (worst-across-axes) row
    assert all(a["path"] != "safe" for a in attention)
    paths = [a["path"] for a in attention]
    assert "single" in paths
