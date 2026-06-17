"""Tests for the /assess dogfood golden baseline (Phase 0 of assess-dogfooded).

These guard the captured regression baseline itself: that the golden fixtures
are complete (every block the report's prose depends on is present), that the
normalization is idempotent and actually masks the volatile fields, and that the
loaders work. Part 3's decomposition parity test reuses `golden.normalize_*` and
`golden.load_*` from here - so a break in this scaffolding surfaces before the
decomposition work depends on it.
"""
from __future__ import annotations

import golden

# Blocks the report's prose sections read from run-context.json. The test
# strategy for task 1 names these explicitly: a golden missing any of them would
# let a decomposed pipeline silently drop a section and still "pass" parity.
EXPECTED_BLOCKS = (
    "derived_findings",
    "attention",
    "behaviour",
    "documentation",
    "understanding",
    "runtime",
    "structure",
)


def test_golden_run_context_has_all_expected_blocks() -> None:
    ctx = golden.load_golden_run_context()
    for block in EXPECTED_BLOCKS:
        assert block in ctx, f"golden run-context missing {block!r}"


def test_golden_run_context_volatile_fields_are_normalized() -> None:
    ctx = golden.load_golden_run_context()
    assert ctx["plugin_version"] == golden.SENTINEL
    assert ctx["prior_plugin_version"] == golden.SENTINEL
    assert ctx["run_date"] == golden.SENTINEL
    mc = ctx["measured_commit"]
    assert mc["head_sha"] == golden.SENTINEL
    assert mc["committed_date"] == golden.SENTINEL
    # Structural fields survive normalization.
    assert mc["available"] is True


def test_golden_run_context_keeps_derived_findings_shape() -> None:
    """All eight keyhole findings present in fixed order - the contract the
    report's findings section and Part 1's deterministic surfacing rely on. The
    E1/E2 trust-axis findings (untrusted_hotspot, self_referential_tests) join
    the original six between unexplained_complexity and orphaned_understanding."""
    ctx = golden.load_golden_run_context()
    names = [f["name"] for f in ctx["derived_findings"]]
    assert names == [
        "hidden_coupling",
        "lying_map",
        "unexplained_complexity",
        "untrusted_hotspot",
        "self_referential_tests",
        "orphaned_understanding",
        "candidate_dead_weight",
        "refactor_boundary",
    ]


def test_golden_run_context_has_deterministic_keyhole_products() -> None:
    """Part 1 adds three deterministic report-skeleton products to the bus: the
    pre-rendered findings markdown, the keyhole readiness summary (reported
    alongside the 0-8 score), and the mandatory attention-derived Top-3
    actions."""
    ctx = golden.load_golden_run_context()
    assert ctx["findings_markdown"].startswith(
        "## Cross-Layer Findings (Keyhole Readiness)"
    )
    assert set(ctx["keyhole_summary"]) == {
        "concerns", "safe_zones", "total_concerns", "summary_text"
    }
    assert isinstance(ctx["prescribed_actions"], list)


def test_normalize_run_context_is_idempotent() -> None:
    ctx = golden.load_golden_run_context()
    assert golden.normalize_run_context(ctx) == ctx


def test_normalize_run_context_does_not_mutate_input() -> None:
    ctx = {"plugin_version": "9.9.9", "measured_commit": {"head_sha": "abc", "available": True}}
    snapshot = {"plugin_version": "9.9.9", "measured_commit": {"head_sha": "abc", "available": True}}
    golden.normalize_run_context(ctx)
    assert ctx == snapshot


def test_golden_report_has_normalized_provenance() -> None:
    report = golden.load_golden_report()
    assert f"_Generated {golden.SENTINEL}._" in report
    assert f"- **Measured at commit:** {golden.SENTINEL}" in report
    # The report still carries its substantive sections (the scorecard table
    # now lives inside the 📊 fold rather than under a bare ## AI Readiness).
    assert "## Top 3 Actions" in report
    assert "Full scorecard" in report
    assert "| Layer | What it asks |" in report


def test_normalize_report_is_idempotent() -> None:
    report = golden.load_golden_report()
    assert golden.normalize_report(report) == report


def test_report_has_single_cross_layer_findings_heading() -> None:
    """The 'Cross-Layer Findings (Keyhole Readiness)' heading appears exactly
    once: `findings_markdown` owns it, and the report writer places framing
    prose directly under it instead of adding a duplicate framing heading
    (issue #164)."""
    report = golden.load_golden_report()
    assert report.count("Cross-Layer Findings (Keyhole Readiness)") == 1


def test_load_bearing_surface_is_outside_folds() -> None:
    """The two-audience report keeps a short, picture-led human surface while
    keeping every verbose section present in the raw markdown inside collapsed
    <details> folds (an agent reading the file still sees all of it).

    A section-ablation A/B established the split: the score headline and the
    Top 3 Actions are load-bearing (folding the Top 3 made a fresh agent act on
    the wrong item), so they must stay on the visible surface; the verbose
    scorecard / findings / framing are foldable. This guard fails loudly if that
    split ever regresses in either direction.
    """
    report = golden.load_golden_report()
    surface, sep, folded = report.partition("<details>")
    assert sep, "report must contain at least one <details> fold"

    # Load-bearing: must be on the default-visible surface, never folded.
    assert "## Top 3 Actions" in surface
    assert "Score: 6.0 / 8" in surface
    # The two SVG snapshots carry the human value up top.
    assert surface.count("![") >= 2

    # Foldable verbose detail must live inside a fold, never on the surface.
    for marker in (
        "| Layer | What it asks |",          # the 9-layer scorecard table
        "## Cross-Layer Findings",            # the keyhole findings block
        "How to read this report",            # the framing/method preamble
    ):
        assert marker not in surface, f"{marker!r} leaked onto the visible surface"
        assert marker in folded, f"{marker!r} missing from the folded detail"


def test_opening_summary_is_bespoke_not_boilerplate() -> None:
    """The first thing a human reads (the line under the score headline) must be
    a bespoke, strength-led summary of *this* run - not the old fixed caveat that
    printed identically under every score. The non-verdict reassurance still
    lives in the 'How to read' fold; it must not lead the report.
    """
    report = golden.load_golden_report()
    surface, _, folded = report.partition("<details>")
    boilerplate = "This is an improvement roadmap, not a verdict"
    assert boilerplate not in surface, (
        "the fixed 'not a verdict' caveat must not lead the report - the opening "
        "is a bespoke, strength-led summary"
    )
    assert boilerplate in folded, "the non-verdict framing should remain in the 'How to read' fold"


def test_golden_has_structure_drift_block_with_both_tiers() -> None:
    """The captured baseline carries the structure_drift block (Tier 0 + Tier 1).

    This repo declares an ownership map (the lib README seam doc + the README
    cross-links), so Tier 0 is available; the static import graph exists, so
    Tier 1 is available too. The block's shape is pinned here so a future
    pipeline change that drops or reshapes it fails loudly.
    """
    ctx = golden.load_golden_run_context()
    assert "structure_drift" in ctx, "golden run-context missing structure_drift"
    sd = ctx["structure_drift"]

    t0 = sd["tier_0"]
    assert t0["available"] is True
    assert isinstance(t0["empty_ownership_patterns"], list)
    assert isinstance(t0["total_patterns"], int)
    assert isinstance(t0["matched_patterns"], int)
    # Every empty-pattern row has the documented {pattern, declared_in, owners}.
    for row in t0["empty_ownership_patterns"]:
        assert set(row) == {"pattern", "declared_in", "owners"}

    t1 = sd["tier_1"]
    assert t1["available"] is True
    for key in (
        "human_grouped_static_splits",
        "human_split_static_fuses",
        "human_grouped_never_cochange",
        "human_split_but_cochange",
        "human_static_agree",
        "human_cochange_agree",
    ):
        assert isinstance(t1[f"{key}_count"], int)
    assert t1["seam_allowlist_applied"] is True
    assert isinstance(t1["allowlist_pairs_count"], int)


def test_golden_structure_drift_tier1_has_no_false_positive_seam() -> None:
    """After the seam allowlist this repo surfaces no hidden-coupling seam.

    The documented seams (lib<->tests, build<->skills) are absorbed by the
    allowlist, and the version hot-file's repo-wide couplings don't recur as a
    directory pair - so the drift-derived hidden_coupling contribution is empty.
    The captured derived hidden_coupling finding therefore carries only the
    pre-existing containment-derived directories, never a drift false positive.
    """
    ctx = golden.load_golden_run_context()
    hc = next(f for f in ctx["derived_findings"] if f["name"] == "hidden_coupling")
    # The version-hot-file directory must never appear as a hidden-coupling seam.
    assert ".claude-plugin" not in hc["paths"]


def test_agent_assess_block_is_not_duplicated() -> None:
    """The 'read the .assess/ directory' block is for agents and belongs only in
    the Machine-readable fold - it used to also appear in the Strengths fold.
    """
    report = golden.load_golden_report()
    assert report.count("the `.assess/` directory is actionable feedback written for you") == 1
