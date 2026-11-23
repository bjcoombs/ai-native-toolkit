"""The /assess guided tour (#1102): the sample repository, its one fix and its report.

The sample (``tests/fixtures/tour_sample/``) must land on Step 2 with one gate blocking
Step 3, ``ci_runs_tests``; the tour's fix must move it to Step 3 and no further; and the same
build must give the same report byte for byte, so the cold-reader test in CI reads one text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import assess_tour


@pytest.fixture(scope="module")
def walked(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """One tour walked in one directory: build, run, fix, run again."""
    repo = assess_tour.build(tmp_path_factory.mktemp("walk") / "tally")
    before = assess_tour.run(repo)
    before_report = Path(before["report"]).read_text(encoding="utf-8")
    how = assess_tour.fix(repo)
    after = assess_tour.run(repo)
    after_report = Path(after["report"]).read_text(encoding="utf-8")
    return {"repo": repo, "before": before, "before_report": before_report,
            "how": how, "after": after, "after_report": after_report}


def test_sample_lands_on_step_2_with_one_gate_next(walked: dict[str, object]) -> None:
    before = walked["before"]
    assert isinstance(before, dict)
    assert (before["step"], before["next_step"], before["next_gates"]) == (2, 3, ["ci_runs_tests"])
    report = walked["before_report"]
    assert isinstance(report, str)
    assert "Step 2 of 4:" in report
    assert "Next: make CI run the tests." in report
    assert "First run." in report


def test_the_fix_moves_the_step_by_exactly_one(walked: dict[str, object]) -> None:
    assert walked["how"] == "applied"
    after = walked["after"]
    assert isinstance(after, dict)
    assert (after["step"], after["next_step"], after["next_gates"]) == (3, 4, ["coverage_gate"])
    report = walked["after_report"]
    assert isinstance(report, str)
    assert "Step 3 of 4:" in report
    # The re-run credits the gate the fix closed.
    assert "- Gates gained: CI runs tests" in report


def test_a_second_fix_commits_nothing(walked: dict[str, object]) -> None:
    repo = walked["repo"]
    assert isinstance(repo, Path)
    assert assess_tour.fix(repo) == "already_fixed"


def test_the_same_build_gives_the_same_report(walked: dict[str, object], tmp_path: Path) -> None:
    again = assess_tour.build(tmp_path / "tally")
    first = assess_tour.run(again)
    assert Path(first["report"]).read_text(encoding="utf-8") == walked["before_report"]
    assert assess_tour.fix(again) == "applied"
    second = assess_tour.run(again)
    assert Path(second["report"]).read_text(encoding="utf-8") == walked["after_report"]


def test_a_fix_made_by_hand_is_committed(tmp_path: Path) -> None:
    repo = assess_tour.build(tmp_path / "tally")
    ci = repo / ".github" / "workflows" / "ci.yml"
    ci.write_bytes(ci.read_bytes().replace(b"pytest || true", b"pytest"))
    assert assess_tour.fix(repo) == "edited"
    after = assess_tour.run(repo)
    assert (after["step"], after["next_gates"]) == (3, ["coverage_gate"])


def test_a_fix_that_cannot_apply_is_refused(tmp_path: Path) -> None:
    repo = assess_tour.build(tmp_path / "tally")
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    env = assess_tour._git_env("A <a@example.com>", "2026-10-01T00:00:00+00:00")
    assess_tour._git(repo, env, "commit", "-q", "-a", "-m", "Replace the workflow")
    with pytest.raises(ValueError, match="does not apply"):
        assess_tour.fix(repo)


def test_main_runs_each_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "tally"
    assert assess_tour.main(["build", str(repo)]) == 0
    assert assess_tour.main(["run", str(repo)]) == 0
    assert assess_tour.main(["fix", str(repo)]) == 0
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert out[1]["step"] == 2
    assert out[2]["fix"] == "applied"
    # A non-empty destination is refused with exit 2, not a traceback.
    assert assess_tour.main(["build", str(repo)]) == 2
    assert "not empty" in capsys.readouterr().err


def test_report_writes_the_sample_report_to_a_known_path(tmp_path: Path) -> None:
    out = tmp_path / "out" / "sample-report.md"
    assert assess_tour.main(["report", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Deterministic Assessment Snapshot: tally")
    fixed = tmp_path / "fixed.md"
    result = assess_tour.report(fixed, fixed=True)
    assert result["step"] == 3
    assert "Step 3 of 4:" in fixed.read_text(encoding="utf-8")


def test_a_missing_sample_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(assess_tour, "SAMPLE_DIR", tmp_path / "absent")
    assert assess_tour.main(["report", "--out", str(tmp_path / "r.md")]) == 2
    assert "not installed" in capsys.readouterr().err
