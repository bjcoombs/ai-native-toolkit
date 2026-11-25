"""The sweep's project sync: Status, Size and Verdict from the verdict (issue #1327).

Replays recorded `gh project` output with no network and checks the Status
table row by row, the waiting-on-you rule, the no-op second run, dry-run,
the write cap and the project-shape check.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

from refine_write_env import FIXTURE_REPO, settings

import refine_project as rp  # noqa: E402  (refine_write_env puts the scripts on sys.path)

READY, IN_PROGRESS = "agent-ready", "in-progress"
BOT = {"login": "github-actions[bot]", "type": "Bot"}
PERSON = {"login": "maintainer", "type": "User"}

FIELDS = {"fields": [
    {"id": "F_STATUS", "name": "Status", "type": "ProjectV2SingleSelectField",
     "options": [{"id": f"O_{s}", "name": s} for s in rp.STATUSES]},
    {"id": "F_SIZE", "name": "Size", "type": "ProjectV2Field"},
    {"id": "F_VERDICT", "name": "Verdict", "type": "ProjectV2SingleSelectField",
     "options": [{"id": f"O_{v}", "name": v} for v in rp.VERDICTS]},
]}


def entry(number: int, verdict: str = "needs_judgement", labels: Sequence[str] = (), size: Optional[int] = None,
          overlaps: Sequence[Dict[str, Any]] = (), blocked_by: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    return {"number": number, "url": f"https://github.com/{FIXTURE_REPO}/issues/{number}", "verdict": verdict,
            "labels": list(labels), "size_points": size, "overlaps": list(overlaps), "blocked_by": list(blocked_by)}


def item(number: int, status: Optional[str] = None, size: Optional[int] = None,
         verdict: Optional[str] = None, repo: str = FIXTURE_REPO) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": f"PVTI_{number}",
                           "content": {"type": "Issue", "number": number, "repository": repo}}
    for key, value in (("status", status), ("size", size), ("verdict", verdict)):
        if value is not None:
            out[key] = value
    return out


class FakeProjectGh:
    """Serves `gh project view/field-list/item-list`; records item-add and item-edit."""

    def __init__(self, items: List[Dict[str, Any]], fields: Optional[Dict[str, Any]] = None) -> None:
        self.items = items
        self.fields = fields if fields is not None else FIELDS
        self.writes: List[List[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        sub = args[1]
        if sub == "view":
            return json.dumps({"id": "PVT_1"})
        if sub == "field-list":
            return json.dumps(self.fields)
        if sub == "item-list":
            return json.dumps({"items": self.items, "totalCount": len(self.items)})
        self.writes.append(list(args))
        return json.dumps({"id": f"PVTI_new{len(self.writes)}"}) if sub == "item-add" else "{}"


def project(tmp_path: Path, gh: FakeProjectGh, live: bool = True, max_writes: int = 500) -> "rp.Project":
    return rp.Project(3, "acme", FIXTURE_REPO, settings(tmp_path, live=live), gh, max_writes)


# ── the Status table ─────────────────────────────────────────────────────

@pytest.mark.parametrize("e, waiting, expected", [
    (entry(1, "ready", [READY, IN_PROGRESS], overlaps=[{"pr": 9, "kind": "closes", "files": []}]), set(), "In review"),
    (entry(1, "ready", [READY, IN_PROGRESS], overlaps=[{"pr": 9, "kind": "files", "files": ["a"]}]), set(),
     "In progress"),
    (entry(1, "ready", [READY], blocked_by=[{"number": 2, "state": "open"}]), {1}, "Blocked"),
    (entry(1, "ready", [READY], blocked_by=[{"number": 2, "state": "closed"}]), set(), "Ready"),
    (entry(1, "needs_judgement", [READY]), {1}, "Ready"),
    (entry(1, "not_ready", [READY], blocked_by=[{"number": 2, "state": "open"}]), {1}, "Waiting on you"),
    (entry(1, "ready", []), {1}, "Waiting on you"),
    (entry(1, "ready", []), set(), "Needs refinement"),
    (entry(1, "not_ready", [READY]), set(), "Needs refinement"),
])
def test_status_is_the_first_matching_row(e: Dict[str, Any], waiting: set, expected: str) -> None:
    assert rp.status_for(e, READY, IN_PROGRESS, waiting) == expected


def _is_bot(user: Any) -> bool:
    return isinstance(user, dict) and user.get("type") == "Bot"


def test_waiting_issues_are_named_by_the_bot_and_not_yet_answered() -> None:
    comments = [
        {"user": BOT, "body": rp.QUESTIONS_MARKER + "\nQuestions:\n\n- #10: which reading?\n- #11: split it?"},
        {"user": PERSON, "body": "#10: reading A"},
        {"user": PERSON, "body": "- #12: a person's list is not a question"},
        {"user": BOT, "body": "<!-- refine:ready-buffer -->\n- #13: not a questions comment"},
        {"user": BOT, "body": rp.QUESTIONS_MARKER + "\n\n- #14: and this one?"},
    ]
    assert rp.waiting_issues(comments, _is_bot) == {11, 14}


def test_a_question_asked_again_after_an_answer_is_open_again() -> None:
    comments = [
        {"user": BOT, "body": rp.QUESTIONS_MARKER + "\n- #10: first"},
        {"user": PERSON, "body": "answered #10"},
        {"user": BOT, "body": rp.QUESTIONS_MARKER + "\n- #10: still unclear"},
    ]
    assert rp.waiting_issues(comments, _is_bot) == {10}


# ── the sync ─────────────────────────────────────────────────────────────

def _edits(gh: FakeProjectGh) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for w in gh.writes:
        if w[1] == "item-edit":
            out[w[w.index("--field-id") + 1]] = w
    return out


def test_sets_status_size_and_verdict_on_an_existing_item(tmp_path: Path) -> None:
    gh = FakeProjectGh([item(5, status="Needs refinement")])
    p = project(tmp_path, gh)
    rp.sync(p, {"issues": [entry(5, "ready", [READY], size=3)]}, READY, IN_PROGRESS, set())
    edits = _edits(gh)
    assert set(edits) == {"F_STATUS", "F_SIZE", "F_VERDICT"}
    assert edits["F_STATUS"][-2:] == ["--single-select-option-id", "O_Ready"]
    assert edits["F_SIZE"][-2:] == ["--number", "3"]
    assert edits["F_VERDICT"][-2:] == ["--single-select-option-id", "O_ready"]
    assert all(w[w.index("--id") + 1] == "PVTI_5" and w[w.index("--project-id") + 1] == "PVT_1"
               for w in edits.values())


def test_a_second_run_with_nothing_changed_writes_nothing(tmp_path: Path) -> None:
    gh = FakeProjectGh([item(5, status="Ready", size=3, verdict="ready")])
    assert rp.sync(project(tmp_path, gh), {"issues": [entry(5, "ready", [READY], size=3)]},
                   READY, IN_PROGRESS, set()) == 0
    assert gh.writes == []


def test_a_size_that_went_away_is_cleared(tmp_path: Path) -> None:
    gh = FakeProjectGh([item(5, status="Needs refinement", size=5, verdict="not_ready")])
    rp.sync(project(tmp_path, gh), {"issues": [entry(5, "not_ready")]}, READY, IN_PROGRESS, set())
    assert _edits(gh)["F_SIZE"][-1] == "--clear"
    assert set(_edits(gh)) == {"F_SIZE"}


def test_a_missing_issue_is_added_then_set(tmp_path: Path) -> None:
    gh = FakeProjectGh([item(5, repo="other/repo")])
    rp.sync(project(tmp_path, gh), {"issues": [entry(5, "not_ready")]}, READY, IN_PROGRESS, set())
    assert gh.writes[0][:2] == ["project", "item-add"]
    assert gh.writes[0][gh.writes[0].index("--url") + 1].endswith("/issues/5")
    assert set(_edits(gh)) == {"F_STATUS", "F_VERDICT"}, "no size to clear on a new item"
    assert all(w[w.index("--id") + 1] == "PVTI_new1" for w in _edits(gh).values())


def test_dry_run_sends_no_write_and_logs_each_one(tmp_path: Path) -> None:
    gh = FakeProjectGh([])
    p = project(tmp_path, gh, live=False)
    rp.sync(p, {"issues": [entry(5, "ready", [READY], size=2)]}, READY, IN_PROGRESS, set())
    assert gh.writes == []
    log = [json.loads(line) for line in p.s.actions_log.read_text(encoding="utf-8").splitlines()]
    assert [r["action"] for r in log] == ["project-add", "project-field", "project-field", "project-field"]
    assert {r["mode"] for r in log} == {"dry-run"}


def test_the_write_cap_refuses_the_rest(tmp_path: Path) -> None:
    gh = FakeProjectGh([item(5), item(6)])
    p = project(tmp_path, gh, max_writes=4)
    rp.sync(p, {"issues": [entry(5, "ready", [READY], size=1), entry(6, "ready", [READY], size=1)]},
            READY, IN_PROGRESS, set())
    assert len(gh.writes) == 4 and p.refused == 2


@pytest.mark.parametrize("drop", ["Status", "Size", "Verdict"])
def test_a_missing_field_stops_before_any_write(tmp_path: Path, drop: str) -> None:
    gh = FakeProjectGh([item(5)], {"fields": [f for f in FIELDS["fields"] if f["name"] != drop]})
    with pytest.raises(rp.ProjectError, match=drop):
        project(tmp_path, gh)


def test_a_missing_status_option_stops_before_any_write(tmp_path: Path) -> None:
    fields = json.loads(json.dumps(FIELDS))
    fields["fields"][0]["options"] = [o for o in fields["fields"][0]["options"] if o["name"] != "Blocked"]
    with pytest.raises(rp.ProjectError, match="Blocked"):
        project(tmp_path, FakeProjectGh([], fields))


# ── command line ─────────────────────────────────────────────────────────

def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra: str) -> None:
    for key in ("REFINE_PROJECT_TOKEN", "REFINE_QUESTIONS_ISSUE", "REFINE_MODE", "CLAUDE_CODE_SCRIPT_CAPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GH_REPO", FIXTURE_REPO)
    monkeypatch.setenv("REFINE_STATE_DIR", str(tmp_path / "state"))
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


def _verdict(tmp_path: Path) -> str:
    path = tmp_path / "verdict.json"
    path.write_text(json.dumps({"issues": [entry(5, "ready", [], size=2)]}), encoding="utf-8")
    return str(path)


def test_main_reads_open_questions_and_writes_waiting_on_you(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path, REFINE_MODE="live", REFINE_QUESTIONS_ISSUE="40")
    comments = json.dumps([{"user": BOT, "body": rp.QUESTIONS_MARKER + "\n- #5: which reading?"}])
    reads: List[List[str]] = []

    def read(args: Sequence[str]) -> str:
        reads.append(list(args))
        return comments

    gh = FakeProjectGh([item(5)])
    code = rp.main(["--verdict", _verdict(tmp_path), "--project-number", "3", "--owner", "acme"], read, gh)
    assert code == 0
    assert reads and reads[0][-1].startswith(f"repos/{FIXTURE_REPO}/issues/40/comments")
    assert _edits(gh)["F_STATUS"][-1] == "O_Waiting on you"


def test_main_without_a_project_token_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    assert rp.main(["--verdict", _verdict(tmp_path), "--project-number", "3", "--owner", "acme"]) == 2


def test_main_reports_a_failed_gh_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)

    def broken(args: Sequence[str]) -> str:
        raise rp.GhError("gh project view failed: HTTP 401")

    assert rp.main(["--verdict", _verdict(tmp_path), "--project-number", "3", "--owner", "acme"],
                   project_runner=broken) == 3


def test_main_exits_4_when_the_cap_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path, REFINE_PROJECT_MAX_WRITES="1")
    assert rp.main(["--verdict", _verdict(tmp_path), "--project-number", "3", "--owner", "acme"],
                   project_runner=FakeProjectGh([item(5)])) == 4
