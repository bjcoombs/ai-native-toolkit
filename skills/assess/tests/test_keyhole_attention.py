"""Keyhole-signal unit tests for one finding family.

Covers assemble_findings, candidate dead weight, the attention list and its
tie-break, the integrate() wiring of attention, unactioned_intent and
untrusted_hotspot, and the FINDING_MODES registry.

The pure, git-free derivation core; end-to-end wiring (build_run_context)
is covered in test_assess_core.py. Split from the former
test_keyhole_signals.py by finding family; shared integrate() fixtures live
in keyhole_helpers.py.
"""
from __future__ import annotations

from pathlib import Path

from keyhole_helpers import _finding_paths
from lib import keyhole_signals as ks


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


def test_attention_tie_break_orders_hotspot_rank_then_severity_then_path() -> None:
    """Five score-1 rows: top_hotspots members lead in hotspot rank order (here
    the reverse of path order), then descending marker severity, then path."""
    findings = ks.assemble_findings({
        "unactioned_intent": [
            "src/zeta.py", "src/mid.py", "src/beta.py", "src/gamma.py", "src/alpha.py",
        ],
    })
    tie_break = ks.attention_tie_break(
        {"top_hotspots": [{"path": "src/zeta.py"}, {"path": "src/mid.py"}]},
        {"top_offenders": [
            {"path": "src/alpha.py", "severity": 6.0},
            {"path": "src/beta.py", "severity": 11.0},
            {"path": "src/gamma.py", "severity": 3.0},
            {"path": "src/gamma.py", "severity": 9.0},  # the file's highest counts
        ]},
        {},
    )
    attention = ks.build_attention_list(findings, tie_break=tie_break)
    assert [(u["path"], u["score"]) for u in attention] == [
        ("src/zeta.py", 1), ("src/mid.py", 1),
        ("src/beta.py", 1), ("src/gamma.py", 1), ("src/alpha.py", 1),
    ]


def test_attention_tie_break_never_outranks_score() -> None:
    """The tie-break only orders equal scores: a score-2 row still leads."""
    findings = ks.assemble_findings({
        "unactioned_intent": ["hot.py", "cold.py"],
        "lying_map": ["cold.py"],
    })
    tie_break = ks.attention_tie_break({"top_hotspots": [{"path": "hot.py"}]}, None, {})
    ranked = [u["path"] for u in ks.build_attention_list(findings, tie_break=tie_break)]
    assert ranked == ["cold.py", "hot.py"]


def test_attention_tie_break_hidden_coupling_lower_containment_first() -> None:
    """Hidden-coupling severity is 1 - containment_ratio: the directory whose
    commits bleed out most leads; a drift-only directory falls back to
    containment_by_dir, and equal severity falls through to path."""
    findings = ks.assemble_findings({"hidden_coupling": ["a", "b", "c", "d"]})
    tie_break = ks.attention_tie_break(
        {"top_hotspots": []},
        None,
        {
            "hidden_coupling_findings": [
                {"path": "a", "containment_ratio": 0.4},
                {"path": "b", "containment_ratio": 0.1},
                {"path": "c", "containment_ratio": 0.4},
            ],
            "containment_by_dir": {"d": 0.0},
        },
    )
    ranked = [u["path"] for u in ks.build_attention_list(findings, tie_break=tie_break)]
    assert ranked == ["d", "b", "a", "c"]


def test_attention_tie_break_mixed_marker_and_coupling_rows_share_one_scale() -> None:
    """Marker severity (at least 5 for a stale marker) and coupling severity
    (0-1) meet in one sort. On a shared 0-1 scale a fully bleeding seam ranks
    with the worst marker file instead of below every marker file, and at the
    attention cap coupling rows are not all evicted by marker rows."""
    markers = [f"m{i}.py" for i in range(10)]
    findings = ks.assemble_findings({
        "unactioned_intent": markers,
        "hidden_coupling": ["bleeds", "tight"],
    })
    tie_break = ks.attention_tie_break(
        {"top_hotspots": []},
        {"top_offenders": [
            {"path": p, "severity": 50.0 - i} for i, p in enumerate(markers)
        ]},
        {"hidden_coupling_findings": [
            {"path": "bleeds", "containment_ratio": 0.0},
            {"path": "tight", "containment_ratio": 0.9},
        ]},
    )
    ranked = [u["path"] for u in ks.build_attention_list(findings, tie_break=tie_break)]
    assert ranked[:2] == ["bleeds", "m0.py"]  # both 1.0; path breaks the tie
    assert len(ranked) == ks.MAX_ATTENTION_UNITS
    assert "bleeds" in ranked and "tight" not in ranked  # 0.1 sits below m8.py (0.84)


def test_attention_tie_break_absent_falls_back_to_path() -> None:
    findings = ks.assemble_findings({"unactioned_intent": ["b.py", "a.py"]})
    assert [u["path"] for u in ks.build_attention_list(findings)] == ["a.py", "b.py"]


def test_integrate_attention_tie_break_uses_top_hotspots(tmp_path: Path) -> None:
    """integrate threads top_hotspots and marker severity into the ranking."""
    pm = {
        "available": True, "aging_reliable": True,
        "stale_by_file": {"a.py": {}, "b.py": {}, "c.py": {}},
        "top_offenders": [
            {"path": "a.py", "severity": 1.0}, {"path": "b.py", "severity": 5.0},
        ],
    }
    out = ks.integrate(
        repo_root=tmp_path,
        complexity_stats={"top_hotspots": [{"path": "c.py"}]},
        doc_staleness={}, dead_code={}, observability={}, structure={},
        promissory_markers=pm,
    )
    assert [u["path"] for u in out["attention"]] == ["c.py", "b.py", "a.py"]


def test_integrate_unactioned_intent_silent_when_aging_unreliable(tmp_path: Path) -> None:
    """Thin history reads "not assessed": stale markers never become a finding."""
    pm = {"available": True, "aging_reliable": False,
          "stale_by_file": {"a.py": {}}, "top_offenders": []}
    out = ks.integrate(
        repo_root=tmp_path, complexity_stats={}, doc_staleness={},
        dead_code={}, observability={}, structure={}, promissory_markers=pm,
    )
    assert _finding_paths(out, "unactioned_intent") == []
    pm["aging_reliable"] = True
    out = ks.integrate(
        repo_root=tmp_path, complexity_stats={}, doc_staleness={},
        dead_code={}, observability={}, structure={}, promissory_markers=pm,
    )
    assert _finding_paths(out, "unactioned_intent") == ["a.py"]


def test_integrate_untrusted_hotspot_wired_from_test_pressure(tmp_path: Path) -> None:
    """test_pressure reaches the E1 finding; absent, the finding is silent."""
    stats = {"top_hotspots": [{"path": "src/hot.py"}]}
    pressure = {"per_file": [{"file": "src/hot.py", "survived": 4, "total": 10}]}
    common = dict(repo_root=tmp_path, complexity_stats=stats, doc_staleness={},
                  dead_code={}, observability={}, structure={})
    assert _finding_paths(
        ks.integrate(**common, test_pressure=pressure), "untrusted_hotspot"
    ) == ["src/hot.py"]
    assert _finding_paths(ks.integrate(**common), "untrusted_hotspot") == []


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
