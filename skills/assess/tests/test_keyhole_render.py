"""Keyhole-signal unit tests for one finding family.

Covers render_findings_markdown, build_keyhole_summary, and
build_prescribed_actions / render_prescribed_actions.

The pure, git-free derivation core; end-to-end wiring (build_run_context)
is covered in test_assess_core.py. Split from the former
test_keyhole_signals.py by finding family; shared integrate() fixtures live
in keyhole_helpers.py.
"""
from __future__ import annotations

from pathlib import Path

from lib import keyhole_signals as ks


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


def test_render_findings_markdown_no_marker_when_paths_at_cap() -> None:
    n = ks.MAX_FINDING_PATHS_RENDERED
    paths = [f"docs/{i}.md" for i in range(n)]
    findings = ks.assemble_findings({"lying_map": paths})
    md = ks.render_findings_markdown(findings, [])
    assert "omitted" not in md


def test_render_findings_markdown_discloses_omitted_paths() -> None:
    n = ks.MAX_FINDING_PATHS_RENDERED + 7
    paths = [f"docs/{i}.md" for i in range(n)]
    findings = ks.assemble_findings({"lying_map": paths})
    md = ks.render_findings_markdown(findings, [])
    listed = [ln for ln in md.splitlines() if ln.startswith("- docs/")]
    assert len(listed) + 7 == n
    assert (
        "_... 7 more omitted; full list in `.assess/run-context.json` "
        "`derived_findings` (`lying_map`) `paths`_"
    ) in md


def test_render_findings_markdown_path_bullets_are_exactly_the_paths() -> None:
    """Omission markers never start with ``- ``, so a consumer parsing bullets
    gets exactly the rendered paths and attention rows, nothing more."""
    paths = [f"docs/{i}.md" for i in range(ks.MAX_FINDING_PATHS_RENDERED + 3)]
    units = [f"u{i}" for i in range(ks.MAX_ATTENTION_UNITS + 4)]
    findings = ks.assemble_findings({
        "lying_map": paths,
        "hidden_coupling": units,
        "unexplained_complexity": units,
    })
    attention = ks.build_attention_list(findings)
    md = ks.render_findings_markdown(findings, attention)
    bullets = [ln[2:].split(" (")[0] for ln in md.splitlines()
               if ln.startswith("- ")]
    cap = ks.MAX_FINDING_PATHS_RENDERED
    expected = [p for f in findings for p in f["paths"][:cap]]
    expected += [a["path"] for a in attention[:ks.MAX_ATTENTION_ROWS_RENDERED]]
    assert bullets == expected
    assert md.count("more omitted") == 4  # three findings plus attention


def test_render_findings_markdown_attention_no_marker_at_cap() -> None:
    n = ks.MAX_ATTENTION_ROWS_RENDERED
    findings = ks.assemble_findings({
        "hidden_coupling": [f"u{i}" for i in range(n)],
        "lying_map": [f"u{i}" for i in range(n)],
    })
    md = ks.render_findings_markdown(findings, ks.build_attention_list(findings))
    assert "omitted" not in md


def test_render_findings_markdown_attention_discloses_omitted_rows() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": [f"u{i}" for i in range(8)],
        "lying_map": [f"u{i}" for i in range(8)],
    })
    attention = ks.build_attention_list(findings)
    assert len(attention) < ks.MAX_ATTENTION_UNITS  # stored array is complete
    md = ks.render_findings_markdown(findings, attention)
    omitted = len(attention) - ks.MAX_ATTENTION_ROWS_RENDERED
    assert omitted > 0
    assert (
        f"_... {omitted} more omitted; full list in "
        "`.assess/run-context.json` `attention`_"
    ) in md
    assert "ranked rows" not in md


def test_render_findings_markdown_attention_at_unit_cap_says_top_n() -> None:
    """At the build_attention_list cap, run-context holds only the top N."""
    units = [f"u{i}" for i in range(ks.MAX_ATTENTION_UNITS + 4)]
    findings = ks.assemble_findings(
        {"hidden_coupling": units, "lying_map": units})
    attention = ks.build_attention_list(findings)
    assert len(attention) == ks.MAX_ATTENTION_UNITS
    md = ks.render_findings_markdown(findings, attention)
    omitted = ks.MAX_ATTENTION_UNITS - ks.MAX_ATTENTION_ROWS_RENDERED
    assert (
        f"_... {omitted} more omitted; top {ks.MAX_ATTENTION_UNITS} "
        "ranked rows in `.assess/run-context.json` `attention`_"
    ) in md
    assert "full list in `.assess/run-context.json` `attention`" not in md


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


def test_attention_low_signal_when_top_score_is_one() -> None:
    """Every row in one finding only: the ranking is weak, so it is flagged."""
    findings = ks.assemble_findings({"lying_map": [f"u{i}" for i in range(5)]})
    attention = ks.build_attention_list(findings)
    assert ks.is_attention_low_signal(attention) is True


def test_attention_low_signal_false_when_any_row_scores_two() -> None:
    findings = ks.assemble_findings({
        "hidden_coupling": ["worst"],
        "lying_map": ["worst", "u1", "u2", "u3"],
    })
    assert ks.is_attention_low_signal(ks.build_attention_list(findings)) is False


def test_attention_low_signal_false_on_empty_attention() -> None:
    """No rows means nothing to cap: the flag stays false."""
    assert ks.is_attention_low_signal([]) is False


def test_integrate_attention_low_signal_caps_prescribed_at_one(tmp_path: Path) -> None:
    """Five score-1 rows: flag true, one prescribed action (rank 1), attention intact."""
    pm = {
        "available": True, "aging_reliable": True,
        "stale_by_file": {f"u{i}.py": {} for i in range(5)},
        "top_offenders": [],
    }
    out = ks.integrate(
        repo_root=tmp_path, complexity_stats={"top_hotspots": [{"path": "u3.py"}]},
        doc_staleness={}, dead_code={}, observability={}, structure={},
        promissory_markers=pm,
    )
    assert out["attention_low_signal"] is True
    assert len(out["attention"]) == 5
    assert [(p["path"], p["rank"]) for p in out["prescribed_actions"]] == [("u3.py", 1)]


def test_build_prescribed_actions_attention_low_signal_false_keeps_three() -> None:
    """One score-2 row: flag false, prescribed_actions built as before (three)."""
    findings = ks.assemble_findings({
        "hidden_coupling": ["u0.py"],
        "unactioned_intent": [f"u{i}.py" for i in range(5)],
    })
    attention = ks.build_attention_list(findings)
    assert ks.is_attention_low_signal(attention) is False
    assert len(ks.build_prescribed_actions(attention, findings)) == 3


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
