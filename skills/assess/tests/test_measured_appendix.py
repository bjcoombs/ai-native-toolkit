"""The "How this was measured" report section lists the scans that ran and were skipped (#794)."""
from __future__ import annotations

import inspect

import pytest

from lib.fleet.fleet_diagrams import report_markdown
from lib.report.measured_appendix import CATALOG_URL, HEADING, measured_section
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


def test_a_central_permission_rule_gets_its_evidence_line() -> None:
    """#1153: a run under --managed-settings that credited managed permissions says so."""
    central = _doc(agent_ops={"summary": {"permissions_encoded": True, "permissions_source": "managed"}})
    local = _doc(agent_ops={"summary": {"permissions_encoded": True, "permissions_source": "repository"}})
    text = measured_section([central, local], ["agent_ops"])
    assert ("- `agent_ops`: permissions set centrally (managed settings on this machine) in 1 of 2"
            in text)
    assert "set centrally" not in measured_section([local], ["agent_ops"])


def test_a_truncated_issue_tracker_read_is_stated() -> None:
    # #1323 criterion 5, placed by #1324: a capped read is said to be partial, with its reasons.
    whole = _doc(backlog_ready={"available": True, "close_open_ratio": 1.0, "outcomes": {"truncated": False}})
    window = _doc(backlog_ready={"available": True, "close_open_ratio": None,
                                 "close_open_ratio_reason": "window_truncated", "outcomes": {"truncated": True}})
    opened = _doc(backlog_ready={"available": True, "untriaged_after_7d_share": None,
                                 "untriaged_after_7d_share_reason": "open_truncated",
                                 "outcomes": {"available": True, "truncated": True}})
    capped = _doc(backlog_ready={"available": True, "outcomes": {"truncated": True}})
    text = measured_section([whole, window, opened], ["backlog_ready"])
    assert ("- `backlog_ready`: the issue tracker read stopped at its page cap in 2 of 3 (open_truncated, "
            "window_truncated: the metric it would bias is not measured), so its counts cover part of "
            "the tracker") in text
    assert not banned_hits(text)
    # #1628: a capped issue timeline alone sets outcomes.truncated; no issue list stopped at its cap.
    only = measured_section([capped], ["backlog_ready"])
    assert "- `backlog_ready`: a tracker read was cut short in 1 of 1, so its counts" in only
    assert "page cap" not in only and not banned_hits(only)
    mixed = measured_section([whole, window, capped], ["backlog_ready"])
    assert ("- `backlog_ready`: a tracker read was cut short in 2 of 3 (window_truncated: the metric it "
            "would bias is not measured), so its counts cover part of the tracker") in mixed
    assert "page cap" not in measured_section([whole], ["backlog_ready"])
    assert "page cap" not in measured_section([_doc(ci_checks={"outcomes": {"truncated": True}})], ["ci_checks"])


def test_no_repository_scanned() -> None:
    text = measured_section([])
    assert text.startswith(f"{HEADING}\n\nIn place: the catalog of every check. No repository was scanned")


def test_fleet_report_appends_the_section_last() -> None:
    text = report_markdown([], measured=measured_section([_doc()], ["ci_checks"]))
    assert text.rstrip().endswith("- `ci_checks`: not selected by --scans.")
    assert HEADING not in report_markdown([])


def test_a_partial_skip_names_its_reasons() -> None:
    """#918: a scan that ran in one repository and was skipped in another says why."""
    ran = _doc(review_reality={"reviewed": 3})
    skipped = _doc(review_reality={"available": False, "reason": "network: reads GitHub"})
    text = measured_section([ran, skipped, _doc()], ["review_reality"], absent="not here")
    assert "- `review_reality`: skipped in 2 of 3 (network, not here)" in text
    assert "In place: over 3 repositories, 0 scans were measured everywhere, 1 ran" in text
    planned = _doc(review_reality={"available": False, "reason": "not_run: not run under profile citizen"})
    assert "skipped in 1 of 2 (not run under profile citizen)" in measured_section([ran, planned], ["review_reality"])


def test_the_skip_codes_are_the_full_run_only_reasons() -> None:
    """#918: the appendix reads signals.FULL_RUN_ONLY_REASONS rather than listing its own."""
    from lib.report import measured_appendix
    from lib.run.scan_plan import NOT_RUN_CODE
    from lib.run.signals import FULL_RUN_ONLY_REASONS
    assert measured_appendix.SKIP_CODES == FULL_RUN_ONLY_REASONS | {NOT_RUN_CODE}
    source = inspect.getsource(measured_appendix)
    assert '"not_registered"' not in source and '"network"' not in source


@pytest.mark.parametrize("text", [
    '[guide](../missing.md "Guide")',
    "[guide](<../missing file.md>)",
    "[guide](../a(v1)/missing.md 'Guide')",
    "see https://example.test/a(v1)/missing for more",
    "<https://example.test/missing>",
])
def test_a_whole_link_destination_or_url_is_not_prose(text: str) -> None:
    """#918: a complete Markdown destination, title included, and a URL with brackets are addresses."""
    assert not banned_hits(text), text
    assert banned_hits(f"{text} and a missing word")
