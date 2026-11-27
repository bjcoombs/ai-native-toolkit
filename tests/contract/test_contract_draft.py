"""Tests for drafting a run's acceptance contract from the ready queue (issue #1328).

- Draft: a fixture queue of three ready issues yields a draft whose criteria
  map one-to-one to their Success Criteria items, each with its command
  (``test_three_ready_issues_map_one_to_one``).
- Kill test: the generated kill test passes on the draft and fails on a draft
  with one criterion's command removed (``test_kill_test_*``).
- Approval: ``start_gate.py`` refuses a draft that is not on the default branch
  and passes once the draft is committed there and frozen
  (``test_start_gate_refuses_unmerged_draft_then_passes_once_merged``).
- Refresh: adding one issue to and removing one from the queue gives a
  refreshed draft and a change summary naming both; an unchanged queue leaves
  the draft byte-identical (``test_refresh_*``).

No AI, no network.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "contract"))
sys.path.insert(0, str(REPO / "skills" / "refine" / "scripts"))

import draft  # noqa: E402
import draft_approval  # noqa: E402
import freeze  # noqa: E402
import refine_queue  # noqa: E402
import start_gate as sg  # noqa: E402
from refine_definition import load_definition  # noqa: E402

RUN_ID = "issues-agent-ready"
DEFINITION = load_definition(REPO / "docs" / "contributing" / "definition-of-ready.md")

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _issue(number: int, criteria: List[str], comments: Any = (), labels: Any = ("agent-ready",)) -> Dict[str, Any]:
    body = "\n".join([
        "## Problem Statement", "p", "## Technical Context", "t", "## Impact", "i",
        "## Root Cause", "r", "## Solution Requirements", "s", "## Success Criteria",
        *["- [ ] %s" % c for c in criteria],
    ])
    return {
        "number": number, "title": "Issue %d" % number, "body": body, "state": "open",
        "state_reason": None, "labels": list(labels), "created_at": "2026-11-20T00:00:00Z",
        "closed_at": None, "url": "https://example.test/issues/%d" % number, "blocked_by": [],
        "sub_issues_total": 0, "comments": list(comments),
    }


CLARIFIED = {
    "body": "<!-- triage:clarified-scope -->\n## Success Criteria\n"
            "- `gh api repos/o/r/issues/12/sub_issues | jq 'length'` prints `0`\n"
            "- `make test-contract` exits 0\n",
    "created_at": "2026-11-21T00:00:00Z",
    "trusted": True,
}


def _backlog_issues() -> List[Dict[str, Any]]:
    return [
        _issue(11, ["`python3 tool.py --version` exits 0", "New test `tests/test_tool.py` passes"]),
        _issue(12, ["works well"], comments=[CLARIFIED]),
        _issue(13, ["`make lint` exits 0"]),
        _issue(14, ["`make docs` exits 0"], labels=("needs-triage",)),  # not ready: not queued
    ]


def _queue(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(refine_queue.build_queue("acme/widgets", issues, [], DEFINITION))


def _write(tmp: Path, issues: List[Dict[str, Any]]) -> str:
    return draft.write(_queue(issues), RUN_ID, tmp)


def _kill_test(tmp: Path) -> Dict[str, Any]:
    return json.loads(draft.kill_test_path(tmp, RUN_ID).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Draft generation (#1371).
# --------------------------------------------------------------------------- #


def test_three_ready_issues_map_one_to_one(tmp_path: Path) -> None:
    queue = _queue(_backlog_issues())
    assert [i["number"] for i in queue["issues"]] == [11, 12, 13]
    _write(tmp_path, _backlog_issues())
    text = draft.contract_path(tmp_path, RUN_ID).read_text(encoding="utf-8")
    contract = freeze.parse_contract_text(text)
    expected = [(i["number"], k, item) for i in queue["issues"] for k, item in enumerate(i["criteria"], 1)]
    assert [c.id for c in contract.criteria] == ["I%d-%d" % (n, k) for n, k, _ in expected]
    for crit, (number, _, item) in zip(contract.criteria, expected):
        assert (item[4:] if item.startswith("[ ] ") else item) in crit.action and "#%d" % number in crit.action
        assert crit.tier == 1
    # The clarified-scope comment replaced issue 12's vague body criterion.
    assert "works well" not in text and "`make test-contract`" in text
    assert draft.is_draft(text) and contract.cls == "cli"


def test_issue_with_an_unanchored_item_is_left_out_and_listed(tmp_path: Path) -> None:
    issues = _backlog_issues() + [_issue(21, ["`make x` exits 0", "works well"])]
    summary = _write(tmp_path, issues)
    text = draft.contract_path(tmp_path, RUN_ID).read_text(encoding="utf-8")
    ids = [c.id for c in freeze.parse_contract_text(text).criteria]
    assert not any(i.startswith("I21-") for i in ids) and "I11-1" in ids
    assert "- #21 Issue 21: item 2 names no command" in text
    assert "- Not drafted #21 Issue 21: item 2 names no command" in summary
    assert draft.check(text, _kill_test(tmp_path)) == []


def test_task_list_boxes_are_dropped_so_ticking_one_changes_nothing(tmp_path: Path) -> None:
    _write(tmp_path, _backlog_issues())
    before = draft.contract_path(tmp_path, RUN_ID).read_bytes()
    assert b"[ ]" not in before
    ticked = _backlog_issues()
    ticked[2]["body"] = ticked[2]["body"].replace("- [ ] `make lint`", "- [x] `make lint`")
    assert _write(tmp_path, ticked).startswith("No change")
    assert draft.contract_path(tmp_path, RUN_ID).read_bytes() == before


def test_empty_queue_is_nothing_to_draft(tmp_path: Path) -> None:
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"repository": "a/b", "issues": []}), encoding="utf-8")
    assert draft.main(["write", "--queue", str(q), "--run-id", RUN_ID, "--contract-dir", str(tmp_path)]) == 3
    assert not draft.contract_path(tmp_path, RUN_ID).exists()


def test_draft_is_never_frozen_automatically(tmp_path: Path) -> None:
    _write(tmp_path, _backlog_issues())
    result = freeze.freeze(draft.contract_path(tmp_path, RUN_ID), RUN_ID,
                           draft.kill_test_path(tmp_path, RUN_ID), contract_dir=tmp_path)
    assert not result.frozen
    assert any("passed_against_null" in r for r in result.reasons)
    assert not (tmp_path / ("%s.completion.json" % RUN_ID)).exists()


def test_kill_test_passes_on_the_draft(tmp_path: Path) -> None:
    _write(tmp_path, _backlog_issues())
    assert draft.main(["check", "--run-id", RUN_ID, "--contract-dir", str(tmp_path)]) == 0


def test_kill_test_fails_when_one_criterion_loses_its_command(tmp_path: Path, capsys: Any) -> None:
    _write(tmp_path, _backlog_issues())
    path = draft.contract_path(tmp_path, RUN_ID)
    text = path.read_text(encoding="utf-8")
    assert text.count("`make lint`") == 1
    path.write_text(text.replace("`make lint`", "the linter"), encoding="utf-8")
    assert draft.main(["check", "--run-id", RUN_ID, "--contract-dir", str(tmp_path)]) == 1
    assert "I13-1 lost its command(s): make lint" in capsys.readouterr().err


def test_kill_test_fails_on_a_dropped_or_extra_criterion(tmp_path: Path) -> None:
    _write(tmp_path, _backlog_issues())
    text = draft.contract_path(tmp_path, RUN_ID).read_text(encoding="utf-8")
    kill = _kill_test(tmp_path)
    dropped = dict(kill, results=kill["results"][:-1])
    assert draft.check(text, dropped) == ["criteria not in the kill test: I13-1"]
    assert draft.check("no yaml here", kill)[0].startswith("the draft does not parse")


# --------------------------------------------------------------------------- #
# Approval by merge (#1372).
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _freeze_draft(contracts: Path) -> None:
    kill = _kill_test(contracts)
    for entry in kill["results"]:
        entry["passed_against_null"] = False  # every criterion fails on the default branch
    filled = contracts.parent / "kill-filled.json"
    filled.write_text(json.dumps(kill), encoding="utf-8")
    result = freeze.freeze(draft.contract_path(contracts, RUN_ID), RUN_ID, filled, contract_dir=contracts)
    assert result.frozen, result.reasons


@needs_git
def test_start_gate_refuses_unmerged_draft_then_passes_once_merged(tmp_path: Path, capsys: Any) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "refine/contract-draft")
    contracts = repo / ".claude" / "contracts"
    _write(contracts, _backlog_issues())
    _git(repo, "add", ".claude/contracts")
    _git(repo, "commit", "-q", "-m", "draft")
    _freeze_draft(contracts)
    args = [RUN_ID, "--contract-dir", str(contracts), "--approved-ref", "main"]

    assert sg.main(args) == 1
    assert "is not on main: merge its pull request" in capsys.readouterr().err

    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "refine/contract-draft")
    assert sg.main(args) == 0

    path = draft.contract_path(contracts, RUN_ID)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert sg.main(args) == 1
    assert "differs from the approved draft" in capsys.readouterr().err


@needs_git
def test_freeze_of_another_contract_is_not_approval(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    contracts = repo / ".claude" / "contracts"
    _write(contracts, _backlog_issues())
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "draft")
    record = {"freeze_evidence": {"contract_hash": "sha256:" + "0" * 64}}
    reason = draft_approval.refusal(contracts, RUN_ID, record, "main")
    assert reason is not None and "different contract" in reason


@needs_git
def test_unresolvable_default_branch_refuses(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    contracts = repo / ".claude" / "contracts"
    _write(contracts, _backlog_issues())
    reason = draft_approval.refusal(contracts, RUN_ID, {}, None)
    assert reason is not None and "--approved-ref" in reason


@needs_git
def test_crlf_checkout_of_the_approved_draft_passes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    contracts = repo / ".claude" / "contracts"
    _write(contracts, _backlog_issues())
    _git(repo, "add", "-f", ".")
    _git(repo, "commit", "-q", "-m", "draft")
    path = draft.contract_path(contracts, RUN_ID)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))  # what core.autocrlf checks out
    record = {"freeze_evidence": {"contract_hash": freeze.contract_sha256(path)}}
    assert draft_approval.refusal(contracts, RUN_ID, record, "main") is None


def test_hand_written_contract_needs_no_approval(tmp_path: Path) -> None:
    draft.contract_path(tmp_path, RUN_ID).write_text("# hand written\n", encoding="utf-8")
    assert draft_approval.refusal(tmp_path, RUN_ID, {}, None) is None
    assert draft_approval.refusal(tmp_path, "no-contract", {}, None) is None


# --------------------------------------------------------------------------- #
# Refresh on queue change (#1373).
# --------------------------------------------------------------------------- #


def test_refresh_names_the_added_and_removed_issue(tmp_path: Path) -> None:
    first = _write(tmp_path, _backlog_issues())
    assert first.startswith("New contract draft: 5 criteria from 3 issues.")
    changed = [i for i in _backlog_issues() if i["number"] != 11] + [_issue(15, ["`make new` exits 0"])]
    summary = _write(tmp_path, changed)
    assert "- Added #15 Issue 15: I15-1" in summary
    assert "- Removed #11 Issue 11: I11-1, I11-2" in summary
    text = draft.contract_path(tmp_path, RUN_ID).read_text(encoding="utf-8")
    ids = [c.id for c in freeze.parse_contract_text(text).criteria]
    assert "I15-1" in ids and not any(i.startswith("I11-") for i in ids)
    assert draft.check(text, _kill_test(tmp_path)) == []


def test_refresh_names_reworded_criteria(tmp_path: Path) -> None:
    _write(tmp_path, _backlog_issues())
    issues = _backlog_issues()
    issues[2] = _issue(13, ["`make lint` exits 0 with no warnings", "`make type` exits 0"])
    summary = _write(tmp_path, issues)
    assert "- Changed #13 Issue 13: added I13-2; reworded I13-1" in summary


def test_refresh_with_an_unchanged_queue_is_byte_identical(tmp_path: Path) -> None:
    _write(tmp_path, _backlog_issues())
    contract_before = draft.contract_path(tmp_path, RUN_ID).read_bytes()
    kill_before = draft.kill_test_path(tmp_path, RUN_ID).read_bytes()
    summary = _write(tmp_path, _backlog_issues())
    assert summary.startswith("No change")
    assert draft.contract_path(tmp_path, RUN_ID).read_bytes() == contract_before
    assert draft.kill_test_path(tmp_path, RUN_ID).read_bytes() == kill_before


def test_cli_write_prints_and_saves_the_summary(tmp_path: Path, capsys: Any) -> None:
    q = tmp_path / "queue.json"
    q.write_text(json.dumps(_queue(_backlog_issues())), encoding="utf-8")
    out = tmp_path / "summary.md"
    rc = draft.main(["write", "--queue", str(q), "--run-id", RUN_ID,
                     "--contract-dir", str(tmp_path / "c"), "--summary", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == capsys.readouterr().out


def test_cli_write_refuses_bad_input(tmp_path: Path) -> None:
    q = tmp_path / "queue.json"
    q.write_text("[]", encoding="utf-8")
    base = ["write", "--queue", str(q), "--run-id", RUN_ID, "--contract-dir", str(tmp_path)]
    assert draft.main(base) == 2
    q.write_text(json.dumps({"issues": [{"number": 1, "criteria": ["vague"]}]}), encoding="utf-8")
    assert draft.main(base) == 3, "no issue whose items all name a command: nothing to draft"
    q.write_text(json.dumps({"issues": [{"number": "x", "criteria": []}]}), encoding="utf-8")
    assert draft.main(base) == 1
    assert draft.main(["check", "--run-id", "../x", "--contract-dir", str(tmp_path)]) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_unreadable_contract_refuses(tmp_path: Path) -> None:
    path = draft.contract_path(tmp_path, RUN_ID)
    path.mkdir(parents=True)  # exists, but reading it raises
    reason = draft_approval.refusal(tmp_path, RUN_ID, {}, None)
    assert reason is not None and "cannot be read" in reason


def test_refine_queue_refuses_a_truncated_listing(tmp_path: Path) -> None:
    class Truncated:
        repo = "acme/widgets"
        open_truncated = True

        def open_issues(self) -> List[Dict[str, Any]]:
            return _backlog_issues()

        def open_pull_requests(self) -> List[Dict[str, Any]]:
            return []

    out = tmp_path / "queue.json"
    rc = refine_queue.main(["--repo", "acme/widgets", "--out", str(out)], backlog=Truncated())  # type: ignore[arg-type]
    assert rc == 3 and not out.exists()


def test_refine_queue_cli_writes_the_queue(tmp_path: Path) -> None:
    class Backlog:
        repo = "acme/widgets"
        open_truncated = False

        def open_issues(self) -> List[Dict[str, Any]]:
            return _backlog_issues()

        def open_pull_requests(self) -> List[Dict[str, Any]]:
            return [{"number": 99, "title": "t", "files": [], "closes": [13]}]

    out = tmp_path / "queue.json"
    rc = refine_queue.main(["--repo", "acme/widgets", "--out", str(out),
                            "--definition", str(REPO / "docs" / "contributing" / "definition-of-ready.md")],
                           backlog=Backlog())  # type: ignore[arg-type]
    assert rc == 0
    queue = json.loads(out.read_text(encoding="utf-8"))
    assert [i["number"] for i in queue["issues"]] == [11, 12]  # 13 is in flight under PR 99


# --------------------------------------------------------------------------- #
# Gaps from #1565: CRLF freeze, code-span whitespace.
# --------------------------------------------------------------------------- #


@needs_git
def test_crlf_checkout_freezes_the_lf_hashed_kill_test_and_starts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    contracts = repo / ".claude" / "contracts"
    _write(contracts, _backlog_issues())
    _git(repo, "add", "-f", ".")
    _git(repo, "commit", "-q", "-m", "draft")
    path = draft.contract_path(contracts, RUN_ID)
    lf_hash = _kill_test(contracts)["contract_sha256"]
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))  # what core.autocrlf checks out
    assert freeze.contract_sha256(path) != lf_hash
    _freeze_draft(contracts)  # the kill test still names the LF hash draft.py wrote
    record = json.loads((contracts / ("%s.completion.json" % RUN_ID)).read_text(encoding="utf-8"))
    # The evidence holds the working copy's bytes, the hash the exit verifier re-hashes.
    assert record["freeze_evidence"]["contract_hash"] == freeze.contract_sha256(path)
    assert sg.main([RUN_ID, "--contract-dir", str(contracts), "--approved-ref", "main"]) == 0


def test_crlf_tolerance_does_not_accept_another_contract(tmp_path: Path) -> None:
    _write(tmp_path, _backlog_issues())
    path = draft.contract_path(tmp_path, RUN_ID)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n").replace(b"make lint", b"make nothing"))
    kill = _kill_test(tmp_path)
    for entry in kill["results"]:
        entry["passed_against_null"] = False
    filled = tmp_path / "kill-filled.json"
    filled.write_text(json.dumps(kill), encoding="utf-8")
    result = freeze.freeze(path, RUN_ID, filled, contract_dir=tmp_path)
    assert not result.frozen and "not bound to this contract" in result.reasons[0]


def test_code_span_whitespace_is_kept_verbatim(tmp_path: Path) -> None:
    item = "`printf 'a  b'`   prints\t`a  b`  and `jq '.x |  length'` exits 0"
    _write(tmp_path, [_issue(31, [item])])
    entry = _kill_test(tmp_path)["results"][0]
    assert entry["commands"] == ["printf 'a  b'", "a  b", "jq '.x |  length'"]
    assert entry["criterion"] == "`printf 'a  b'` prints `a  b` and `jq '.x |  length'` exits 0"
    text = draft.contract_path(tmp_path, RUN_ID).read_text(encoding="utf-8")
    assert draft.check(text, _kill_test(tmp_path)) == []


def test_code_span_across_a_line_break_stays_on_one_line(tmp_path: Path) -> None:
    queue = {"repository": "a/b", "issues": [
        {"number": 32, "title": "t", "url": "u", "criteria": ["`make  check\nlint` exits 0"]}]}
    draft.write(queue, RUN_ID, tmp_path)
    entry = _kill_test(tmp_path)["results"][0]
    assert entry["commands"] == ["make  check lint"]
    text = draft.contract_path(tmp_path, RUN_ID).read_text(encoding="utf-8")
    assert draft.check(text, _kill_test(tmp_path)) == []
