"""The "How this was measured" report section lists the scans that ran and were skipped (#794)."""
from __future__ import annotations

from lib.fleet_diagrams import report_markdown
from lib.measured_appendix import CATALOG_URL, HEADING, measured_section
from lib.vocabulary import banned_hits

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
    assert not banned_hits(text)
    ran, skipped = text.split("### Skipped")
    # agent_ops was skipped in one repository, so it is not measured everywhere either.
    assert ("In place: over 2 repositories, 1 scan was measured everywhere, 2 ran but were not measured "
            "everywhere, and 4 were skipped.") in ran
    assert "### Ran (3)" in ran
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
    full = measured_section([_doc()], ["structure_drift"], absent_code="no_block")
    assert "- `structure_drift`: not measured in 1 of 1 (no_block)" in full
    assert "### Skipped (0)" in full
    partial = measured_section([_doc(agent_ops={}), _doc()], ["agent_ops"])
    assert "0 scans were measured everywhere, 1 ran but was not measured everywhere" in partial
    assert "In place: over 1 repository, 0 scans were measured everywhere, 1 ran" in measured_section([doc], ["ci_checks"])
    assert "### Skipped (0)\n\n- none" in measured_section([doc], ["ci_checks"])


def test_no_repository_scanned() -> None:
    text = measured_section([])
    assert text.startswith(f"{HEADING}\n\nIn place: the catalog of every check. No repository was scanned")


def test_fleet_report_appends_the_section_last() -> None:
    text = report_markdown([], measured=measured_section([_doc()], ["ci_checks"]))
    assert text.rstrip().endswith("- `ci_checks`: not selected by --scans.")
    assert HEADING not in report_markdown([])
