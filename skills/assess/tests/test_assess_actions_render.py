"""assess_actions_render (#1251): the PR table, tracker items and Readiness line come from .assess/ data, never the report."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import assess_actions_render as render

ACTIONS = {
    "schema": 2,
    "run_id": "r1",
    "actions": [
        # Listed out of rank order on purpose: output follows rank, not file order.
        {
            "rank": 3, "action": "Split ghreport.sh along its seams", "layer": 3, "effort": "medium",
            "files": ["skills/ghreport/scripts/ghreport.sh"], "first_step": "rg -n \"^[a-z_]+\\(\\)\" ghreport.sh",
            "done_when": "tests pass unchanged", "scope_fence": "Move functions only",
            "finding": "accretion_ratchet", "status": "pending", "claimed_by": None,
            "completed_sha": None, "mode": "refactor_safe",
        },
        {
            "rank": 1, "action": "Give the E402 suppressions a reason", "layer": 3,
            "condition": "layer-3-linters.2", "effort": "small", "files": ["a.py"],
            "first_step": "rg -n \"noqa: E402\" a.py", "done_when": "ruff check passes | reasons recorded",
            "scope_fence": "Suppression comments only", "finding": "unactioned_intent",
            "status": "pending", "claimed_by": None, "completed_sha": None, "mode": "verify_then_retire",
        },
        {
            "rank": 2, "action": "Do the same for fleet.py", "done_when": "no E402 reported",
            "scope_fence": "Ruff config only",
        },
    ],
    "completed": [
        {"rank": 9, "action": "Done long ago", "done_when": "x", "scope_fence": "y", "status": "done"},
    ],
}

REPORT = """# Report

## Top 3 Actions

| # | Action | Practice | Effort | Command / First Step | Done when | Scope fence | Hotspot files this addresses | Issue |
|---|--------|-------|--------|---------------------|-----------|-------------|------------------------------|-------|
| 1 | A different wording in the report | x | small | y | z | w | v | - |
"""


def _repo(tmp_path: Path, *, report: str | None = REPORT) -> Path:
    assess = tmp_path / ".assess"
    assess.mkdir(parents=True)
    (assess / "actions.json").write_text(json.dumps(ACTIONS), encoding="utf-8")
    if report is not None:
        (assess / "assess-report.md").write_text(report, encoding="utf-8")
    return tmp_path


def _run(repo: Path, *args: str, capsys: pytest.CaptureFixture[str]) -> str:
    assert render.main([str(repo), *args]) == 0
    return capsys.readouterr().out


def test_table_has_one_row_per_entry_in_rank_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(_repo(tmp_path), "table", capsys=capsys)
    rows = out.splitlines()[2:]
    assert len(rows) == 3
    for rank, row in zip((1, 2, 3), rows, strict=True):
        entry = next(a for a in ACTIONS["actions"] if a["rank"] == rank)  # type: ignore[union-attr, index]
        assert row.startswith(f"| {rank} | {entry['action']} |")  # type: ignore[index]
        assert render._cell(str(entry["done_when"])) in row  # type: ignore[index]
        assert render._cell(str(entry["scope_fence"])) in row  # type: ignore[index]
    assert "Done long ago" not in out  # completed entries are not in the Top 3


def test_table_escapes_pipes_and_marks_missing_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = _run(_repo(tmp_path), "table", capsys=capsys).splitlines()
    assert "ruff check passes \\| reasons recorded" in rows[2]
    assert "| 3 (`layer-3-linters.2`) | small |" in rows[2]
    # Rank 2 carries only the required keys: every optional cell and Issue read "-".
    assert rows[3] == "| 2 | Do the same for fleet.py | - | - | - | no E402 reported | Ruff config only | - | - |"
    assert all(r.count(" | ") == 8 for r in rows[2:])  # nine columns, none broken by a field


def test_issue_back_fill_fills_the_issue_column(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(_repo(tmp_path), "table", "--issue", "1=#43", "--issue", "2=#44, #45", capsys=capsys)
    rows = out.splitlines()[2:]
    assert rows[0].endswith("| #43 |")
    assert rows[1].endswith("| #44, #45 |")
    assert rows[2].endswith("| - |")


def test_bad_issue_value_is_rejected() -> None:
    with pytest.raises(SystemExit):
        render.main([".", "table", "--issue", "one=#1"])


def test_items_one_per_entry_with_the_contract_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(_repo(tmp_path), "items", "--report-link", "https://example.test/pr/7", capsys=capsys)
    items = json.loads(out)
    assert [i["rank"] for i in items] == [1, 2, 3]
    first = items[0]
    assert first["title"] == "Give the E402 suppressions a reason"
    body = first["body"]
    assert body.startswith("**Finding:** `unactioned_intent` in `a.py`")
    for part in ("### First step", "rg -n \"noqa: E402\" a.py", "### Done when", "- ruff check passes | reasons recorded",
                 "### Scope fence", "Suppression comments only", "- Layer: 3 (`layer-3-linters.2`)",
                 "- Effort: small", "- Full report: https://example.test/pr/7"):
        assert part in body
    assert body.index("**Finding:**") < body.index("### First step") < body.index("### Done when")
    assert body.rstrip().endswith("https://github.com/bjcoombs/ai-native-toolkit")
    sparse = items[1]["body"]
    assert sparse.startswith("**Finding:** not recorded")
    assert "### First step" not in sparse and "Full report" in sparse


def test_items_omit_the_report_link_when_none_given(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    items = json.loads(_run(_repo(tmp_path), "items", capsys=capsys))
    assert all("Full report" not in i["body"] for i in items)


@pytest.mark.parametrize("command", ["table", "items"])
def test_output_does_not_depend_on_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    with_report = _run(_repo(tmp_path / "a"), command, capsys=capsys)
    without = _run(_repo(tmp_path / "b", report=None), command, capsys=capsys)
    reshaped = _run(_repo(tmp_path / "c", report="# Report\n\nNo table here.\n"), command, capsys=capsys)
    assert with_report == without == reshaped


def test_malformed_entries_are_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assess = tmp_path / ".assess"
    assess.mkdir()
    payload = {"actions": [{"rank": 1, "action": "x"}, "junk", {"rank": "2", "action": "y", "done_when": "d",
               "scope_fence": "s"}, {"rank": 3, "action": "z", "done_when": "d", "scope_fence": "s"}]}
    (assess / "actions.json").write_text(json.dumps(payload), encoding="utf-8")
    out = _run(tmp_path, "table", capsys=capsys)
    assert out.splitlines()[2:] == ["| 3 | z | - | - | - | d | s | - | - |"]


@pytest.mark.parametrize("content", [None, "not json", json.dumps({"actions": "nope"})])
def test_missing_or_unreadable_actions_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    assess = tmp_path / ".assess"
    assess.mkdir()
    if content is not None:
        (assess / "actions.json").write_text(content, encoding="utf-8")
    assert render.main([str(tmp_path), "table"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "actions.json" in captured.err


def test_assess_dir_option_reads_a_scoped_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    scoped = tmp_path / ".assess" / "svc"
    scoped.mkdir()
    (scoped / "actions.json").write_text(json.dumps({"actions": [ACTIONS["actions"][2]]}), encoding="utf-8")  # type: ignore[index]
    out = _run(repo, "--assess-dir", str(scoped), "table", capsys=capsys)
    assert len(out.splitlines()) == 3


def test_readiness_prefers_headline_lines_then_badge(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    assess = repo / ".assess"
    assert _run(repo, "readiness", capsys=capsys) == "not known\n"
    (assess / "badge.json").write_text(json.dumps({"message": "Step 4 · all gates held"}), encoding="utf-8")
    assert _run(repo, "readiness", capsys=capsys) == "Step 4 · all gates held\n"
    context = {"headline": {"lines": ["Step 3 · Unattended agent PRs · contracts in place",
                                      "Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced"]}}
    (assess / "run-context.json").write_text(json.dumps(context), encoding="utf-8")
    assert _run(repo, "readiness", capsys=capsys) == (
        "Step 3 · Unattended agent PRs · contracts in place / "
        "Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced\n"
    )
    (assess / "assess-report.md").unlink()
    assert "Step 3" in _run(repo, "readiness", capsys=capsys)
