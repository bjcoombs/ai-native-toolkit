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


# --- Task 2: render_findings_markdown ----------------------------------------

def _sample_findings() -> list[dict]:
    """Two findings with paths + the positive boundary, the rest empty."""
    return ks.assemble_findings({
        "hidden_coupling": ["dir/a"],
        "lying_map": ["docs/x.md", "docs/y.md"],
        "refactor_boundary": ["island"],
    })


def test_render_findings_markdown_includes_paths_and_actions() -> None:
    findings = _sample_findings()
    attention = ks.build_attention_list(findings)
    md = ks.render_findings_markdown(findings, attention)
    assert md.startswith("## Cross-Layer Findings (Keyhole Readiness)")
    # Only findings with paths render a heading.
    assert "### hidden_coupling" in md
    assert "### lying_map" in md
    assert "### refactor_boundary" in md
    # The deterministic action text appears verbatim.
    assert f"Action: {ks.FINDING_ACTIONS['hidden_coupling']}" in md
    # Every path is listed.
    assert "- docs/x.md" in md
    assert "- docs/y.md" in md
    # Empty findings produce no heading.
    assert "### unexplained_complexity" not in md
    assert md.endswith("\n")


def test_render_findings_markdown_empty_is_minimal_but_valid() -> None:
    findings = ks.assemble_findings({})  # all six/eight empty
    md = ks.render_findings_markdown(findings, [])
    assert md.startswith("## Cross-Layer Findings (Keyhole Readiness)")
    assert "No cross-layer findings surfaced" in md
    # No finding headings when nothing has paths.
    assert "###" not in md


def test_render_findings_markdown_caps_paths_at_ten() -> None:
    findings = ks.assemble_findings({
        "lying_map": [f"docs/{i}.md" for i in range(20)],
    })
    md = ks.render_findings_markdown(findings, [])
    listed = [ln for ln in md.splitlines() if ln.startswith("- docs/")]
    assert len(listed) == ks.MAX_FINDING_PATHS_RENDERED


def test_render_findings_markdown_attention_section_caps_at_five() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": [f"u{i}" for i in range(8)],
        "lying_map": [f"u{i}" for i in range(8)],  # each unit scores 2
    })
    attention = ks.build_attention_list(findings)
    md = ks.render_findings_markdown(findings, attention)
    assert "### Attention List (Priority Order)" in md
    # Attention rows carry the "(score N)" marker; finding path bullets do not.
    rows = [ln for ln in md.splitlines() if ln.startswith("- u") and "(score" in ln]
    assert len(rows) == ks.MAX_ATTENTION_ROWS_RENDERED


# --- Issue #172: degenerate churn drops churn-derived findings ----------------

# A bleeding-but-statically-modular dir -> hidden_coupling; reused below.
_BLEEDING_COMMIT_SETS = [
    {Path("looksmodular/x.py"), Path("core/util.py")},
    {Path("looksmodular/y.py"), Path("core/util.py")},
    {Path("looksmodular/x.py"), Path("core/other.py")},
    {Path("looksmodular/z.py"), Path("core/util.py")},
    {Path("looksmodular/x.py"), Path("shared/s.py")},
]
_MODULAR_STRUCTURE = {
    "available": True, "modularity_q": 0.6, "front_door_ratio": 0.95,
}
# A complex code file under a doc dir, so a stale high-confidence doc -> lying_map.
_COMPLEXITY_STATS = {
    "ccn": {"p50": 3.0, "p95": 8.0, "max": 30.0},
    "files_scored": 1,
    "top_complex": [{"path": "pkg/core.py", "loc": 400, "ccn": 30.0}],
    "top_hotspots": [],
    "top_large": [],
}


def _stale_doc_staleness(*, churn_degenerate: bool) -> dict:
    """A high-confidence stale doc over pkg/core.py - a lying_map when the churn
    history is real, suppressed when it is degenerate."""
    return {
        "available": True,
        "churn_window": "commits (last 12mo)",
        "churn_degenerate": churn_degenerate,
        "docs": [{
            "path": "pkg/README.md",
            "ratio": 6.0,
            "last_commit_days": 10,
            "doc_churn_in_window": 1,
            "code_churn_in_window": 6,
            "subject_code_count": 1,
            "subject_method": "nearest-ancestor",
            "confidence": "high",
        }],
    }


def _integrate(*, churn_degenerate: bool) -> dict:
    return ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=churn_degenerate),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
    )


def _finding_paths(result: dict, name: str) -> list[str]:
    return next(f["paths"] for f in result["derived_findings"] if f["name"] == name)


def test_real_churn_produces_churn_derived_findings() -> None:
    """Baseline: with real churn (not degenerate), the lying_map and
    hidden_coupling findings fire and are counted in the keyhole summary."""
    result = _integrate(churn_degenerate=False)
    assert _finding_paths(result, "lying_map") == ["pkg/README.md"]
    assert _finding_paths(result, "hidden_coupling") == ["looksmodular"]
    counted = {c["name"] for c in result["keyhole_summary"]["concerns"]}
    assert {"lying_map", "hidden_coupling"} <= counted


def test_degenerate_churn_drops_churn_derived_findings_from_summary() -> None:
    """Issue #172: on a degenerate churn history the same inputs yield zero
    churn-derived findings - lying_map (confidence capped in the join) and
    hidden_coupling (dropped here) are absent from derived_findings AND the
    keyhole_summary, so a reader sees 0 lying maps from a meaningless signal."""
    result = _integrate(churn_degenerate=True)
    assert _finding_paths(result, "lying_map") == []
    assert _finding_paths(result, "hidden_coupling") == []
    counted = {c["name"] for c in result["keyhole_summary"]["concerns"]}
    assert "lying_map" not in counted
    assert "hidden_coupling" not in counted


# --- Task 3: build_keyhole_summary -------------------------------------------

def test_build_keyhole_summary_counts_concerns_and_safe_zones() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": ["a", "b"],
        "lying_map": ["c"],
        "refactor_boundary": ["s1", "s2", "s3"],
    })
    summary = ks.build_keyhole_summary(findings)
    assert summary["safe_zones"] == 3
    assert summary["total_concerns"] == 3
    by_name = {c["name"]: c["count"] for c in summary["concerns"]}
    assert by_name == {"hidden_coupling": 2, "lying_map": 1}
    # The summary text is a pure count, parallel to the 0-8 score - never a score.
    assert summary["summary_text"] == (
        "3 structural concerns (2 hidden coupling, 1 lying map), 3 safe zones."
    )


def test_build_keyhole_summary_empty_findings_neutral_message() -> None:
    summary = ks.build_keyhole_summary(ks.assemble_findings({}))
    assert summary["concerns"] == []
    assert summary["total_concerns"] == 0
    assert summary["safe_zones"] == 0
    assert summary["summary_text"] == "No structural concerns, 0 safe zones."


def test_build_keyhole_summary_singular_plural() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": ["a"],
        "refactor_boundary": ["s1"],
    })
    summary = ks.build_keyhole_summary(findings)
    assert summary["summary_text"] == (
        "1 structural concern (1 hidden coupling), 1 safe zone."
    )


def test_build_keyhole_summary_no_concerns_with_safe_zones() -> None:
    findings = ks.assemble_findings({"refactor_boundary": ["s1", "s2"]})
    summary = ks.build_keyhole_summary(findings)
    assert summary["summary_text"] == "No structural concerns, 2 safe zones."


def test_build_keyhole_summary_hyphenates_self_referential_tests() -> None:
    """Compound-adjective finding names get an explicit display name
    ('self-referential tests'), not a naive underscore->space replace."""
    findings = ks.assemble_findings({"self_referential_tests": ["a", "b"]})
    summary = ks.build_keyhole_summary(findings)
    assert summary["summary_text"] == (
        "2 structural concerns (2 self-referential tests), 0 safe zones."
    )


def test_finding_display_name_map_with_space_fallback() -> None:
    """Mapped names use the display-name override; unmapped names fall back to
    the plain underscore->space replace."""
    assert ks.finding_display_name("self_referential_tests") == "self-referential tests"
    assert ks.finding_display_name("hidden_coupling") == "hidden coupling"
    assert ks.finding_display_name("some_future_finding") == "some future finding"


# --- Task 4: build_prescribed_actions / render_prescribed_actions ------------

def test_build_prescribed_actions_picks_worst_finding_per_unit() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": ["worst"],
        "lying_map": ["worst", "mid"],
        "unexplained_complexity": ["worst", "mid"],
    })
    attention = ks.build_attention_list(findings)
    prescribed = ks.build_prescribed_actions(attention, findings)
    # 'worst' lands in 3 findings (score 3) -> ranks first.
    assert prescribed[0]["path"] == "worst"
    assert prescribed[0]["rank"] == 1
    # 'worst' spans hidden_coupling + lying_map + unexplained; hidden_coupling
    # is highest severity (earliest in FINDING_ORDER).
    assert prescribed[0]["action"] == ks.FINDING_ACTIONS["hidden_coupling"]
    # 'mid' lands in lying_map + unexplained_complexity; lying_map is worse.
    mid = next(p for p in prescribed if p["path"] == "mid")
    assert mid["action"] == ks.FINDING_ACTIONS["lying_map"]


def test_build_prescribed_actions_caps_at_three() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": [f"u{i}" for i in range(5)],
        "lying_map": [f"u{i}" for i in range(5)],
    })
    attention = ks.build_attention_list(findings)
    prescribed = ks.build_prescribed_actions(attention, findings)
    assert len(prescribed) == 3
    assert [p["rank"] for p in prescribed] == [1, 2, 3]


def test_build_prescribed_actions_empty_attention() -> None:
    assert ks.build_prescribed_actions([], ks.assemble_findings({})) == []


def test_render_prescribed_actions_rows_and_empty() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": ["worst"],
        "lying_map": ["worst"],
    })
    attention = ks.build_attention_list(findings)
    prescribed = ks.build_prescribed_actions(attention, findings)
    rows = ks.render_prescribed_actions(prescribed)
    # Seven-column table row matching the SKILL.md Top 3 Actions template.
    assert rows.startswith("| 1 |")
    assert rows.count("|") == 8  # 7 columns => 8 pipes
    assert "`worst`" in rows
    assert ks.render_prescribed_actions([]) == ""


# --- Task 5 E1: find_untrusted_hotspots --------------------------------------

def test_find_untrusted_hotspots_flags_high_survivor_density() -> None:
    complexity_stats = {"top_hotspots": [
        {"path": "src/hot.py"}, {"path": "src/cold.py"},
    ]}
    test_pressure = {"per_file": [
        {"file": "src/hot.py", "survived": 4, "total": 10},   # 0.4 >= 0.3
        {"file": "src/cold.py", "survived": 1, "total": 10},  # 0.1 < 0.3
    ]}
    assert ks.find_untrusted_hotspots(complexity_stats, test_pressure) == ["src/hot.py"]


def test_find_untrusted_hotspots_silent_without_mutation_data() -> None:
    complexity_stats = {"top_hotspots": [{"path": "src/hot.py"}]}
    # No per_file -> no mutation evidence -> nothing flagged (read-only default).
    assert ks.find_untrusted_hotspots(complexity_stats, {"per_file": []}) == []
    assert ks.find_untrusted_hotspots(complexity_stats, {}) == []
    assert ks.find_untrusted_hotspots(complexity_stats, None) == []


def test_find_untrusted_hotspots_only_flags_actual_hotspots() -> None:
    complexity_stats = {"top_hotspots": [{"path": "src/hot.py"}]}
    test_pressure = {"per_file": [
        {"file": "src/not_a_hotspot.py", "survived": 9, "total": 10},
    ]}
    assert ks.find_untrusted_hotspots(complexity_stats, test_pressure) == []


# --- Task 5 E2: test-to-code mapping -----------------------------------------

def test_build_test_to_code_map_finds_colocated_tests(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "pkg").mkdir()
    (repo / "pkg" / "svc.go").write_text("package pkg")
    (repo / "pkg" / "svc_test.go").write_text("package pkg")
    (repo / "pkg" / "lonely.go").write_text("package pkg")  # no sibling test
    mapping = ks.build_test_to_code_map(repo, ["pkg/svc.go", "pkg/lonely.go"])
    assert mapping == {"pkg/svc_test.go": "pkg/svc.go"}


def test_build_test_to_code_map_python_and_adjacent_dir(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "src").mkdir()
    (repo / "src" / "mod.py").write_text("x = 1")
    (repo / "src" / "tests").mkdir()
    (repo / "src" / "tests" / "test_mod.py").write_text("x = 1")
    mapping = ks.build_test_to_code_map(repo, ["src/mod.py"])
    assert mapping == {"src/tests/test_mod.py": "src/mod.py"}


def test_find_sibling_test_skips_test_files_themselves(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "foo_test.go").write_text("package x")
    assert ks._find_sibling_test(repo, "foo_test.go") is None
    assert ks.build_test_to_code_map(repo, ["foo_test.go"]) == {}


# --- Accretion ratchet finding -----------------------------------------------

def _accreting_block(*, reliable: bool = True) -> dict:
    """A minimal well-formed accretion_ratchet run-context block."""
    return {
        "available": True,
        "reliable": reliable,
        "deletion_fraction_threshold": 0.15,
        "total_in_band": 2,
        "files": [
            {
                "path": "src/fat.py",
                "net_additions": 2400,
                "commit_count": 31,
                "deletion_fraction": 0.0,
                "time_span_months": 18.0,
            },
            {
                "path": "lib/bloat.py",
                "net_additions": 800,
                "commit_count": 12,
                "deletion_fraction": 0.05,
                "time_span_months": 6.0,
            },
        ],
    }


def test_accretion_ratchet_finding_returns_paths_worst_first() -> None:
    """Files are returned sorted by net additions descending, then path."""
    run_ctx = {"accretion_ratchet": _accreting_block()}
    paths = ks._accretion_ratchet_finding(run_ctx)
    # net_additions: fat.py=2400 > bloat.py=800
    assert paths == ["src/fat.py", "lib/bloat.py"]


def test_accretion_ratchet_finding_unavailable_returns_empty() -> None:
    """Block with available=False -> no finding paths (graceful degrade)."""
    run_ctx = {"accretion_ratchet": {"available": False, "files": []}}
    assert ks._accretion_ratchet_finding(run_ctx) == []


def test_accretion_ratchet_finding_absent_block_returns_empty() -> None:
    """Missing accretion_ratchet key -> no finding paths."""
    assert ks._accretion_ratchet_finding({}) == []


def test_accretion_ratchet_finding_no_files_returns_empty() -> None:
    """Available block but empty files list -> no finding paths."""
    run_ctx = {"accretion_ratchet": {"available": True, "reliable": True, "files": []}}
    assert ks._accretion_ratchet_finding(run_ctx) == []


def test_accretion_ratchet_finding_action_text_matches_config() -> None:
    """The action string in FINDING_ACTIONS matches the requirement."""
    assert ks.FINDING_ACTIONS["accretion_ratchet"] == (
        "refactor down: extract, delete dead code, or split the file"
    )


def test_format_accretion_items_roll_up_and_per_file_lines() -> None:
    """Roll-up sentence + one line per file in worst-first order."""
    run_ctx = {"accretion_ratchet": _accreting_block()}
    items = ks._format_accretion_items(run_ctx)
    # First item is the roll-up sentence.
    assert items[0].startswith("2 files show monotonic growth")
    assert "2 hottest" in items[0]
    # Per-file lines include path, LOC, time span, commits, deletion fraction.
    assert any("src/fat.py" in line and "+2,400 LOC" in line for line in items)
    assert any("lib/bloat.py" in line for line in items)
    # Worst offender comes first.
    fat_idx = next(i for i, line in enumerate(items) if "src/fat.py" in line)
    bloat_idx = next(i for i, line in enumerate(items) if "lib/bloat.py" in line)
    assert fat_idx < bloat_idx


def test_format_accretion_items_reliable_false_adds_disclaimer() -> None:
    """Unreliable history appends a disclaimer line."""
    run_ctx = {"accretion_ratchet": _accreting_block(reliable=False)}
    items = ks._format_accretion_items(run_ctx)
    assert any("UNRELIABLE" in line for line in items)


def test_format_accretion_items_reliable_true_no_disclaimer() -> None:
    """Reliable history: no disclaimer line."""
    run_ctx = {"accretion_ratchet": _accreting_block(reliable=True)}
    items = ks._format_accretion_items(run_ctx)
    assert not any("UNRELIABLE" in line for line in items)


def test_accretion_ratchet_in_finding_order_and_actions() -> None:
    """accretion_ratchet is present in both FINDING_ORDER and FINDING_ACTIONS."""
    assert "accretion_ratchet" in ks.FINDING_ORDER
    assert "accretion_ratchet" in ks.FINDING_ACTIONS
    # Placement: after unactioned_intent, before orphaned_understanding.
    order = ks.FINDING_ORDER
    ui_idx = order.index("unactioned_intent")
    ar_idx = order.index("accretion_ratchet")
    ou_idx = order.index("orphaned_understanding")
    assert ui_idx < ar_idx < ou_idx


def test_integrate_accretion_ratchet_wired_in() -> None:
    """When accretion_ratchet block is passed to integrate(), the finding fires.

    assemble_findings sorts paths alphabetically for determinism - the finding
    result carries both files regardless of net_additions order.
    """
    accreting = _accreting_block()
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
        accretion_ratchet=accreting,
    )
    paths = _finding_paths(result, "accretion_ratchet")
    # assemble_findings sorts alphabetically: lib/ before src/
    assert paths == sorted(["src/fat.py", "lib/bloat.py"])
    assert "src/fat.py" in paths
    assert "lib/bloat.py" in paths


def test_integrate_accretion_ratchet_absent_is_silent() -> None:
    """Without accretion_ratchet arg, the finding is silent (empty paths)."""
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
    )
    assert _finding_paths(result, "accretion_ratchet") == []


# --- override_contradicts_signals finding (archetype marker vs signals) ------

def test_override_contradicts_in_finding_order_and_actions() -> None:
    """override_contradicts_signals is a named finding, positioned before the
    one positive finding (refactor_boundary stays last)."""
    assert "override_contradicts_signals" in ks.FINDING_ORDER
    assert "override_contradicts_signals" in ks.FINDING_ACTIONS
    order = ks.FINDING_ORDER
    assert order.index("override_contradicts_signals") < order.index("refactor_boundary")
    assert ks.FINDING_ACTIONS["override_contradicts_signals"] == (
        "Review archetype marker - deterministic signals suggest a different "
        "classification"
    )


def test_integrate_override_contradiction_fires_finding() -> None:
    """A contradicting archetype block makes the finding fire against its source."""
    archetype = {
        "available": True,
        "override_contradicts_signals": True,
        "override_source": "CLAUDE.md",
    }
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
        archetype=archetype,
    )
    assert _finding_paths(result, "override_contradicts_signals") == ["CLAUDE.md"]


def test_integrate_override_contradiction_absent_is_silent() -> None:
    """No archetype contradiction -> the finding is silent (empty paths)."""
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
        archetype={"available": True, "override_contradicts_signals": False},
    )
    assert _finding_paths(result, "override_contradicts_signals") == []


# --- config-exclusion disclosure (apply_config_excludes) ---------------------

def test_apply_config_excludes_filters_and_counts() -> None:
    """Excluded finding paths are dropped from findings and returned separately."""
    findings = [
        {"name": "hidden_coupling", "paths": ["vendor/x", "src/a"], "action": "z"},
        {"name": "refactor_boundary", "paths": ["vendor/y"], "action": "z"},
    ]
    filtered, dropped = ks.apply_config_excludes(findings, {"vendor"}, [])
    kept = [p for f in filtered for p in f["paths"]]
    assert kept == ["src/a"]
    assert dropped == ["vendor/x", "vendor/y"]


def test_apply_config_excludes_pattern_match() -> None:
    """A basename glob pattern suppresses matching finding paths."""
    findings = [{"name": "lying_map", "paths": ["docs/gen.md", "docs/hand.md"],
                 "action": "z"}]
    filtered, dropped = ks.apply_config_excludes(findings, set(), ["gen.md"])
    assert filtered[0]["paths"] == ["docs/hand.md"]
    assert dropped == ["docs/gen.md"]


def test_apply_config_excludes_noop_without_config() -> None:
    """No excludes -> findings untouched, nothing dropped."""
    findings = [{"name": "hidden_coupling", "paths": ["a"], "action": "z"}]
    filtered, dropped = ks.apply_config_excludes(findings, set(), [])
    assert filtered == findings
    assert dropped == []


# A self-contained directory: repeatedly touched alone, so it reads as a
# refactor_boundary (the git-log containment view, which scan-level excludes
# never filter).
_ISLAND_COMMIT_SETS = [
    {Path("island/a.py")},
    {Path("island/b.py")},
    {Path("island/a.py")},
    {Path("island/c.py")},
    {Path("island/b.py")},
]


def test_integrate_excludes_suppress_finding_and_report_paths() -> None:
    """A config-excluded finding path is filtered from findings but disclosed.

    The refactor_boundary comes from the git-log containment view, which the
    scan-level exclude never touches - so integrate() is where it is filtered.
    """
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=None,
        commit_sets=_ISLAND_COMMIT_SETS,
        exclude_dirs={"island"},
    )
    # island/ was a refactor_boundary; the exclude filters it out of findings.
    assert "island" not in _finding_paths(result, "refactor_boundary")
    # ...but it is disclosed as a suppressed finding path.
    assert "island" in result["excluded_finding_paths"]


def test_integrate_no_excludes_reports_empty_suppression() -> None:
    """Without excludes, excluded_finding_paths is empty."""
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=None,
        commit_sets=_ISLAND_COMMIT_SETS,
    )
    assert result["excluded_finding_paths"] == []
    assert "island" in _finding_paths(result, "refactor_boundary")


# --- structure drift (Tier 1) folded into hidden_coupling --------------------

def _drift_tier1(pairs: list[tuple[str, str]]) -> dict:
    """A minimal available Tier 1 result carrying only the hidden-seam list."""
    return {
        "available": True,
        "human_split_but_cochange": [
            {"file_a": a, "file_b": b} for a, b in pairs
        ],
    }


def test_drift_hidden_coupling_unavailable_is_empty() -> None:
    """An unavailable Tier 1 result yields no hidden-coupling dirs."""
    assert ks.structure_drift_hidden_coupling_dirs({"available": False}) == []
    assert ks.structure_drift_hidden_coupling_dirs({}) == []


def test_drift_hidden_coupling_recurring_dir_pair_surfaces() -> None:
    """Two trees straddled by >= min_pairs distinct file pairs both surface.

    Two distinct file pairs link src/ and lib/; the pair recurs, so both
    directories read as a genuine hidden seam.
    """
    tier1 = _drift_tier1([
        ("src/a.py", "lib/x.py"),
        ("src/b.py", "lib/y.py"),
    ])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == ["lib", "src"]


def test_drift_hidden_coupling_hub_file_is_not_a_seam() -> None:
    """A single hub file coupling with every tree manufactures no seam.

    The version hot-file shape: one file in cfg/ co-changes with a different
    directory on each pair. Each directory *pair* occurs exactly once, so none
    recurs and no directory surfaces - the version-bump ritual is not drift.
    """
    tier1 = _drift_tier1([
        ("cfg/v.json", "src/a.py"),
        ("cfg/v.json", "lib/b.py"),
        ("cfg/v.json", "docs/c.md"),
    ])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == []


def test_drift_hidden_coupling_ignores_root_and_intra_dir() -> None:
    """Pairs touching the repo root, or within one directory, are ignored.

    A root-level file (dir ``.``) has vacuous containment; an intra-directory
    pair is cohesion, not a cross-tree seam. Neither contributes a finding even
    when repeated.
    """
    tier1 = _drift_tier1([
        ("README.md", "src/a.py"),   # root side -> ignored
        ("README.md", "src/b.py"),   # root side -> ignored
        ("src/c.py", "src/d.py"),    # intra-dir -> ignored
        ("src/e.py", "src/f.py"),    # intra-dir -> ignored
    ])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == []


def test_drift_hidden_coupling_single_pair_below_threshold() -> None:
    """One file pair straddling two trees is below the recurrence threshold."""
    tier1 = _drift_tier1([("src/a.py", "lib/x.py")])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == []


def test_drift_tier1_silent_without_static_graph() -> None:
    """_structure_drift_tier1 returns unavailable when the static graph is out.

    With no import graph there is nothing to disagree with, so Tier 1 is not
    even attempted - the detector is never called.
    """
    out = ks._structure_drift_tier1(
        Path("/nonexistent"),
        {"available": False},
        {"available": True, "change_coupling_pairs": []},
    )
    assert out == {"available": False}


def test_integrate_returns_structure_drift_tier1() -> None:
    """integrate() exposes the Tier 1 result for the orchestrator to serialise.

    With a /nonexistent repo there is no ownership map, so the detector degrades
    to available:False - but the key is present, proving the wiring exists.
    """
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
    )
    assert "structure_drift_tier1" in result
    assert result["structure_drift_tier1"].get("available") in (True, False)


# --- FINDING_MODES / mode_for_finding ---------------------------------------

def test_finding_modes_cover_every_finding() -> None:
    """Every named finding maps to a mode - no finding reaches the report
    without a deterministic execution posture."""
    assert set(ks.FINDING_MODES) == set(ks.FINDING_ORDER)


def test_finding_modes_use_only_the_closed_mode_set() -> None:
    """The three modes are a closed vocabulary; no finding invents a fourth."""
    allowed = {"characterize_first", "verify_then_retire", "refactor_safe"}
    assert set(ks.FINDING_MODES.values()) <= allowed
    assert ks.FINDING_MODE_VALUES == frozenset(ks.FINDING_MODES.values())


def test_mode_for_finding_maps_known_types() -> None:
    assert ks.mode_for_finding("lying_map") == "verify_then_retire"
    assert ks.mode_for_finding("refactor_boundary") == "refactor_safe"
    assert ks.mode_for_finding("hidden_coupling") == "characterize_first"


def test_mode_for_finding_defaults_for_unknown_or_missing() -> None:
    """An unknown or absent finding falls back to the conservative default."""
    assert ks.mode_for_finding(None) == ks.DEFAULT_FINDING_MODE
    assert ks.mode_for_finding("not_a_real_finding") == ks.DEFAULT_FINDING_MODE
    assert ks.DEFAULT_FINDING_MODE == "characterize_first"
