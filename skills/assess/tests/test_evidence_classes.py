"""lib/scoring/evidence_classes.py and the report header line it feeds (#1642).

Each condition's evidence class is derived from the input classes of its criteria's signals
(``lib.scoring.signal_grades.InputClass``), never hand-set, and a run names the classes it read
and the ones it did not.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, get_args

import pytest
from assess_test_support import use_real_lizard

import lib.scoring.level_tally as lt
from lib.report.evidence_header import CLASS_WORDS, evidence_line
from lib.run.signals import build_document
from lib.scoring.evidence_classes import (
    EVIDENCE_CLASSES,
    INPUT_EVIDENCE,
    criteria_classes,
    evidence_coverage,
)
from lib.scoring.level_result_types import ConditionResult
from lib.scoring.levels import condition_id
from lib.scoring.signal_grades import SIGNAL_INPUTS, InputClass

FIXTURES = Path(__file__).parent / "fixtures"


def _cond(cid: str, state: str, classes: list[str], reason: str | None = None) -> ConditionResult:
    out: ConditionResult = {"id": cid, "row": cid.rsplit(".", 1)[0], "level": 1, "grade": "measured",
                            "counted": True, "state": state, "evidence_classes": classes}
    if reason is not None:
        out["reason"] = reason
    return out


def test_every_input_class_maps_to_exactly_one_evidence_class() -> None:
    """An input class added to ``InputClass`` without a row in ``INPUT_EVIDENCE`` fails here."""
    assert set(INPUT_EVIDENCE) == set(get_args(InputClass))
    assert all(INPUT_EVIDENCE[c] in EVIDENCE_CLASSES for c in INPUT_EVIDENCE)
    assert set(INPUT_EVIDENCE.values()) == set(EVIDENCE_CLASSES)  # every class has a source


def test_the_mapping_reads_source_control_as_static_and_the_platform_as_practice() -> None:
    assert EVIDENCE_CLASSES == ("static", "runtime", "practice")
    static = {"config_text", "head_text", "head_structure", "git_history", "ci_executed"}
    assert {c for c, e in INPUT_EVIDENCE.items() if e == "static"} == static
    assert INPUT_EVIDENCE["execution_artifact"] == "runtime"
    assert INPUT_EVIDENCE["platform"] == "practice"


def test_criteria_classes_come_from_the_signals_in_class_order_each_once() -> None:
    review = next(s for s, c in SIGNAL_INPUTS.items() if c == "platform")
    text = next(s for s, c in SIGNAL_INPUTS.items() if c == "head_text")
    config = next(s for s, c in SIGNAL_INPUTS.items() if c == "config_text")
    crit = [{"signal": review, "op": "eq", "value": True}, {"signal": text, "op": "eq", "value": True},
            {"signal": config, "op": "eq", "value": True}]
    assert criteria_classes(crit) == ["static", "practice"]  # type: ignore[arg-type]


def test_a_signal_with_no_input_class_gives_no_evidence_class() -> None:
    """A ``--pack`` check has no producer input class, so it adds no class (never a guess)."""
    assert criteria_classes([{"signal": "pack.team.owner", "op": "eq", "value": True}]) == []  # type: ignore[list-item]


def test_every_condition_of_the_default_model_carries_its_classes() -> None:
    model = lt.default_model()
    result = lt.level_result(model, {})
    by_id = {c["id"]: c for c in result["conditions"]}
    for row in model["rows"]:
        for level in row["levels"]:
            cond = by_id[condition_id(row["id"], level["level"])]
            assert cond["evidence_classes"] == criteria_classes(level["criteria"])
            assert cond["evidence_classes"], cond["id"]  # every default signal is classified


def test_no_condition_of_a_shipped_model_mixes_evidence_classes() -> None:
    """Coverage reads per condition, so a condition with two classes could claim one it never read
    (an ``unmet`` from one criterion beside an unread one). The shipped models give each one class."""
    from lib.scoring.levels import load_levels
    profiles = sorted((lt.DEFAULT_LEVELS.parent / "profiles").glob("*.toml"))
    assert profiles
    for path in (lt.DEFAULT_LEVELS, *profiles):
        model = load_levels(path)
        mixed = [condition_id(r["id"], lv["level"]) for r in model["rows"] for lv in r["levels"]
                 if len(criteria_classes(lv["criteria"])) > 1]
        assert mixed == [], (path.name, mixed)


def test_coverage_lists_a_class_read_and_names_the_rest_with_a_reason() -> None:
    conditions = [
        _cond("a.1", "met", ["static"]),
        _cond("b.1", "not_measured", ["runtime"], "needs_full_run"),
        _cond("c.1", "unmet", ["static", "practice"]),
    ]
    assert evidence_coverage(conditions) == {
        "covered": ["static", "practice"],
        "not_covered": [{"evidence_class": "runtime", "reason": "needs_full_run"}],
    }


def test_a_class_no_condition_reads_is_not_covered_with_its_own_reason() -> None:
    cov = evidence_coverage([_cond("a.1", "met", ["static"])])
    assert cov["covered"] == ["static"]
    assert cov["not_covered"] == [{"evidence_class": "runtime", "reason": "no_condition"},
                                  {"evidence_class": "practice", "reason": "no_condition"}]


def test_an_excluded_or_declined_condition_reads_no_class() -> None:
    """Only a value read (met or unmet) covers a class: an exclusion or a decline read nothing."""
    cov = evidence_coverage([_cond("a.1", "not_applicable", ["practice"]),
                             _cond("b.1", "declined", ["runtime"]), _cond("c.1", "met", ["static"])])
    assert cov["covered"] == ["static"]
    assert [e["evidence_class"] for e in cov["not_covered"]] == ["runtime", "practice"]


def test_the_level_result_carries_the_coverage() -> None:
    result = lt.level_result(lt.default_model(), {})
    assert result["evidence_coverage"] == evidence_coverage(result["conditions"])


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


@pytest.fixture(scope="module")
def headless_level(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The default model over a headless run (no ``--platform``) of the golden doc repository."""
    repo = tmp_path_factory.mktemp("evidence") / "repo"
    shutil.copytree(FIXTURES / "golden-doc-repo", repo)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "init")
    with pytest.MonkeyPatch.context() as monkeypatch:
        use_real_lizard(monkeypatch)
        scans = dict(build_document(repo)["scans"])
    return dict(lt.level_result(lt.default_model(), scans))


def test_a_headless_run_without_platform_names_practice_as_not_covered(headless_level: dict[str, Any]) -> None:
    cov = headless_level["evidence_coverage"]
    assert "static" in cov["covered"]
    assert "practice" in [e["evidence_class"] for e in cov["not_covered"]]
    assert "practice" not in cov["covered"]


def test_the_header_line_names_each_class_read_and_each_not_read_with_why() -> None:
    line = evidence_line({"covered": ["static"],
                          "not_covered": [{"evidence_class": "runtime", "reason": "needs_full_run"},
                                          {"evidence_class": "practice", "reason": "needs_platform"}]})
    assert line == (f"Evidence read: static ({CLASS_WORDS['static']}). "
                    "Not read: runtime (needs a full run), practice (needs GitHub or GitLab access).")


def test_the_header_line_with_every_class_read_has_no_not_read_part() -> None:
    line = evidence_line({"covered": list(EVIDENCE_CLASSES), "not_covered": []})
    assert line.startswith("Evidence read: static (") and "Not read" not in line
    assert line.endswith(f"practice ({CLASS_WORDS['practice']}).")


def test_the_header_line_is_empty_without_coverage() -> None:
    """A run context written before #1642 has no coverage: the header stays as it was."""
    assert evidence_line(None) == "" and evidence_line({"covered": "x"}) == ""


def test_a_report_on_a_fixture_run_lists_the_evidence_classes_in_its_header(headless_level: dict[str, Any]) -> None:
    from assess_report import render_report
    ctx = {"run_date": "2026-06-01", "plugin_version": "1.22.0", "prior_stats_exists": False,
           "stats_summary": {"files_scored": 0, "loc": {}, "ccn": {}, "top_hotspots": []},
           "level": headless_level}
    lines = render_report(ctx, "demo").splitlines()
    header = next(ln for ln in lines if ln.startswith("_Generated "))
    assert header.startswith("_Generated 2026-06-01 by `/assess` v1.22.0. Evidence read: static (")
    assert header.endswith("._") and "practice (" in header.split("Not read: ", 1)[1]


def test_a_report_without_a_level_keeps_its_old_header() -> None:
    from assess_report import render_report
    ctx = {"run_date": "2026-06-01", "plugin_version": "1.22.0", "prior_stats_exists": False,
           "stats_summary": {"files_scored": 0, "loc": {}, "ccn": {}, "top_hotspots": []}}
    assert "Evidence read" not in render_report(ctx, "demo")


def test_the_findings_report_template_copies_the_header_line() -> None:
    """Both report paths carry the line: the LLM-written report copies it from assess_report.py."""
    skill = (Path(__file__).resolve().parents[2] / "assess-findings" / "SKILL.md").read_text(encoding="utf-8")
    template = skill.split("# Codebase Assessment: <repo-name>", 1)[1].split("## Top 3 Actions", 1)[0]
    generated = next(ln for ln in template.splitlines() if ln.startswith("_Generated "))
    assert "Evidence read:" in generated and "assess_report.py" in generated


def test_counts_only_keeps_the_classes_and_the_coverage() -> None:
    from lib.counts_only.content_free import counts_only
    level = lt.level_result(lt.default_model(), {})
    kept = counts_only({"level": level})["level"]
    assert kept["evidence_coverage"] == level["evidence_coverage"]
    assert [c["evidence_classes"] for c in kept["conditions"]] == [c["evidence_classes"] for c in level["conditions"]]
