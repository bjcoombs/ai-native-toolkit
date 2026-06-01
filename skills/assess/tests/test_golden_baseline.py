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
    # The report still carries its substantive sections.
    assert "## AI Readiness" in report
    assert "## Top 3 Actions" in report


def test_normalize_report_is_idempotent() -> None:
    report = golden.load_golden_report()
    assert golden.normalize_report(report) == report
