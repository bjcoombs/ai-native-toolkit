"""The "How this was measured" report section lists the scans that ran and were skipped (#794)."""
from __future__ import annotations

from lib.fleet_diagrams import report_markdown
from lib.measured_appendix import CATALOG_URL, HEADING, measured_section

NAMES = ["ci_checks", "structure", "review_reality", "config_drift", "behaviour", "delivery",
         "agent_ops"]


def _doc(**blocks: object) -> dict[str, object]:
    return dict(blocks)


def test_ran_and_skipped_with_reasons() -> None:
    a = _doc(ci_checks={"ci_files": 1}, structure={"available": False, "reason": "no_python_packages"},
             review_reality={"available": False, "reason": "network: reads GitHub"},
             config_drift={"available": False, "reason": "network"},
             behaviour={"available": False, "reason": "not_registered"}, agent_ops={})
    b = _doc(ci_checks={"ci_files": 0}, structure={"modularity_q": 0.4},
             review_reality={"available": False, "reason": "network"},
             config_drift={"available": False, "reason": "network"},
             behaviour={"available": False, "reason": "not_registered"})
    text = measured_section([a, b], NAMES)
    assert text.startswith(HEADING + "\n")
    assert f"[docs/what-we-measure.md]({CATALOG_URL})" in text
    ran, skipped = text.split("### Skipped")
    assert "### Ran (3, over 2 repositories)" in ran
    assert "- `ci_checks`\n" in ran
    assert "- `structure`: not measured in 1 of 2 (no_python_packages)" in ran
    assert "- `agent_ops`: skipped in 1 of 2" in ran
    assert "(4)" in skipped
    assert "- `review_reality`: it reads GitHub or GitLab; run with --platform to measure it." in skipped
    assert "- `config_drift`: it reads GitHub or GitLab, which only a full /assess run does." in skipped
    assert "- `behaviour`: only a full /assess run builds it." in skipped
    assert "- `delivery`: it exists only on the platform; run with --platform to measure it." in skipped


def test_absent_reason_and_partial_lines() -> None:
    doc = _doc(ci_checks={"available": False, "reason": "x"})
    text = measured_section([doc, _doc()], ["ci_checks", "agent_ops"], absent="not here")
    assert "- `ci_checks`: not measured in 1 of 2 (x); skipped in 1 of 2" in text
    assert "- `agent_ops`: not here." in text
    assert "over 1 repository" in measured_section([doc], ["ci_checks"])
    assert "### Skipped (0)\n\n- none" in measured_section([doc], ["ci_checks"])


def test_no_repository_scanned() -> None:
    text = measured_section([])
    assert text.endswith("No repository was scanned, so no scan ran.")


def test_fleet_report_appends_the_section_last() -> None:
    text = report_markdown([], measured=measured_section([_doc()], ["ci_checks"]))
    assert text.rstrip().endswith("- `ci_checks`: not selected by --scans.")
    assert HEADING not in report_markdown([])
