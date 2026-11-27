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
    assert sparse.startswith("### Done when")  # no finding, condition or files: no lead line
    assert "not recorded" not in sparse
    assert "### First step" not in sparse and "Full report" in sparse


@pytest.mark.parametrize(("extra", "lead"), [
    ({"condition": "layer-5-ci.1", "files": ["ci.yml"]}, "**Condition:** `layer-5-ci.1` in `ci.yml`"),
    ({"condition": "layer-5-ci.1"}, "**Condition:** `layer-5-ci.1`"),
    ({"files": ["ci.yml"]}, "**Files:** `ci.yml`"),
    ({"finding": "lying_map"}, "**Finding:** `lying_map`"),
])
def test_item_without_a_finding_leads_with_what_it_has(extra: dict[str, object], lead: str) -> None:
    entry = {"rank": 1, "action": "a", "done_when": "d", "scope_fence": "s", **extra}
    assert render.render_item_body(entry).splitlines()[0] == lead


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


def test_markdown_punctuation_is_escaped_outside_code_spans(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    entry = {"rank": 1, "action": "Split it", "first_step": 'rg -n "^[a-z_]+\\(\\)" x.sh, then `a_b\\c`',
             "done_when": "__init__.py stays <100 lines", "scope_fence": "*only* x.sh"}
    body = render.render_item_body(entry)
    # Outside code spans every \ * _ [ ] < > is escaped, so GitHub shows the text as written.
    assert 'rg -n "^\\[a-z\\_\\]+\\\\(\\\\)" x.sh, then `a_b\\c`' in body
    assert "- \\_\\_init\\_\\_.py stays \\<100 lines" in body
    assert "\\*only\\* x.sh" in body
    row = render.render_table([entry]).splitlines()[2]
    assert "`a_b\\c`" in row and "\\_\\_init" in row
    assert render.render_items([entry])[0]["title"] == "Split it"  # titles are plain text, not Markdown


def test_an_earlier_runs_actions_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    context = repo / ".assess" / "run-context.json"
    context.write_text(json.dumps({"run_id": "r1"}), encoding="utf-8")
    assert render.main([str(repo), "table"]) == 0  # same run
    capsys.readouterr()
    context.write_text(json.dumps({"run_id": "r2"}), encoding="utf-8")
    for command in ("table", "items"):
        assert render.main([str(repo), command]) == 1
        captured = capsys.readouterr()
        assert captured.out == "" and "'r1'" in captured.err and "'r2'" in captured.err
    context.write_text(json.dumps({}), encoding="utf-8")  # a context without a run_id is not a mismatch
    assert render.main([str(repo), "table"]) == 0


def _with_sub_items(tmp_path: Path, sub_items: object) -> Path:
    """One entry carrying ``sub_items`` (#1263) beside one without."""
    assess = tmp_path / ".assess"
    assess.mkdir(parents=True)
    payload = {"schema": 2, "actions": [
        {"rank": 1, "action": "Plain entry", "done_when": "d1", "scope_fence": "s1"},
        {"rank": 2, "action": "Run ruff over every script dir", "done_when": "`make lint` covers skills/assess",
         "scope_fence": "Makefile only", "finding": "unactioned_intent", "files": ["Makefile"], "layer": 3,
         "first_step": "edit the lint target", "sub_items": sub_items},
    ]}
    (assess / "actions.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_a_sub_item_is_filed_as_its_own_item(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sub = {"label": "a", "action": "Add skills/fleet/scripts to the ruff and mypy runs",
           "done_when": "`make lint` runs ruff on skills/fleet/scripts"}
    items = json.loads(_run(_with_sub_items(tmp_path, [sub]), "items", capsys=capsys))
    assert [(i["rank"], i.get("label")) for i in items] == [(1, None), (2, None), (2, "2a")]
    parent, child = items[1], items[2]
    assert parent["title"] == "Run ruff over every script dir"
    assert child["title"] == "2a. Add skills/fleet/scripts to the ruff and mypy runs"
    body = child["body"]
    assert body.startswith("**Finding:** `unactioned_intent` in `Makefile`")
    assert "**Sub-action 2a of action 2:** Run ruff over every script dir" in body
    assert "- `make lint` runs ruff on skills/fleet/scripts" in body
    assert "Makefile only" in body  # the parent's fence when the sub-item sets none
    assert "edit the lint target" not in body  # the parent's first step is the parent's change
    assert "- Layer: 3" in body


def test_entries_with_and_without_sub_items_both_render(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sub = {"label": "2b", "action": "Pin mypy", "done_when": "d", "scope_fence": "pyproject.toml only",
           "first_step": "uv add mypy==1.0"}
    repo = _with_sub_items(tmp_path, [sub])
    rows = _run(repo, "table", capsys=capsys).splitlines()[2:]
    assert [r.split(" | ")[1] for r in rows] == ["Plain entry", "Run ruff over every script dir"]  # one row per entry
    items = json.loads(_run(repo, "items", capsys=capsys))
    assert [i["title"] for i in items] == ["Plain entry", "Run ruff over every script dir", "2b. Pin mypy"]
    assert "pyproject.toml only" in items[2]["body"] and "uv add mypy==1.0" in items[2]["body"]
    assert "Makefile only" not in items[2]["body"]


@pytest.mark.parametrize("sub_items", [
    "not a list", [], ["junk", {"label": "a", "action": "no exit"}, {"action": "x", "done_when": "d"}],
])
def test_malformed_sub_items_are_skipped_not_the_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], sub_items: object
) -> None:
    items = json.loads(_run(_with_sub_items(tmp_path, sub_items), "items", capsys=capsys))
    assert [i["title"] for i in items] == ["Plain entry", "Run ruff over every script dir"]


def test_finalize_keeps_sub_items_for_the_renderer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import assess_finalize

    assess = tmp_path / ".assess"
    assess.mkdir()
    sub = {"label": "a", "action": "Pin ruff", "done_when": "`uv.lock` pins ruff"}
    action = {"rank": 1, "action": "Run ruff", "done_when": "d", "scope_fence": "s", "sub_items": [sub]}
    assess_finalize._write_actions_contract(assess, [action], run_id="r1")
    written = json.loads((assess / "actions.json").read_text(encoding="utf-8"))
    assert written["schema"] == 2 and written["actions"][0]["sub_items"] == [sub]
    items = json.loads(_run(tmp_path, "items", capsys=capsys))
    assert [i["title"] for i in items] == ["Run ruff", "1a. Pin ruff"]
