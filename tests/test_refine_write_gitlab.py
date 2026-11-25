"""The refiner's write guardrails on GitLab, against recorded `glab` output (issue #1327).

`GitLabWriter` (`refine_write_gitlab.py`) runs the GitHub writer's rules over
`glab api`. Each test drives one operation with `FakeGlab`
(`refine_gitlab_env.py`) and asserts which writes reached `glab`.
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from refine_gitlab_env import P, PROJECT, FakeGlab

import refine_apply  # noqa: E402  (refine_gitlab_env put the scripts on sys.path)
import refine_write  # noqa: E402
import refine_write_gitlab  # noqa: E402
from refine_write import Refused  # noqa: E402

BOT = "project_42_bot_abc"
ISSUE_1 = {"P/issues/1": "w_issue_1.json", "P/issues/1/resource_label_events": "label_events_none.json",
           "P/issues/1/resource_state_events": "state_events_none.json", "P/issues/1/notes": "notes_1.json",
           "P/members/all": "members.json", "user": "user_bot.json", "P": "project.json"}
ISSUE_3 = {"P/issues/3": "w_issue_3.json", "P/issues/3/resource_label_events": "label_events_none.json",
           "P/issues/3/resource_state_events": "state_events_none.json", "P/issues/3/notes": "notes_2.json"}


def env(tmp: Path, live: bool = True, **extra: str) -> Dict[str, str]:
    e = {"REFINE_PLATFORM": "gitlab", "CI_PROJECT_PATH": PROJECT, "REFINE_MODE": "live" if live else "dry-run",
         "REFINE_ALLOWED_LABELS": "agent-ready,needs-triage,bug", "REFINE_STATE_DIR": str(tmp / "state")}
    e.update(extra)
    return e


def writer(tmp: Path, routes: Dict[str, str], **kw: Any) -> "tuple[refine_write_gitlab.GitLabWriter, FakeGlab]":
    fake = FakeGlab(routes)
    w = refine_write.make_writer(refine_write.Settings(env(tmp, **kw)), fake)
    assert isinstance(w, refine_write_gitlab.GitLabWriter)
    return w, fake


def verdict(**entry: Any) -> Dict[str, Any]:
    base = {"number": 1, "verdict": "ready", "settled": True, "checklist": [], "overlaps": [], "duplicates": [],
            "unsized": False, "size_points": 2, "labels": ["needs-triage"]}
    base.update(entry)
    return {"definition": {}, "issues": [base], "duplicates": [{"duplicate": 3, "kept": 1, "kind": "exact",
                                                                  "closable": True}]}


def field(args: Any, name: str) -> Optional[str]:
    for i, a in enumerate(args):
        if a == "-f" and args[i + 1].startswith(name + "="):
            return str(args[i + 1].split("=", 1)[1])
    return None


def test_bot_login_comes_from_the_write_token(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, ISSUE_1)
    assert w.s.bot_login == BOT and fake.reads[0] == ["api", "user"]
    w, fake = writer(tmp_path, ISSUE_1, REFINE_BOT_LOGIN="refiner")
    assert w.s.bot_login == "refiner" and fake.reads == []


def test_ready_label_goes_on_and_triage_comes_off(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, ISSUE_1)
    result = w.edit_labels(1, ["agent-ready"], [], verdict())
    assert result == {"added": ["agent-ready"], "removed": ["needs-triage"], "refused": []}
    assert fake.writes == [["api", "-X", "PUT", f"{P}/issues/1", "-f", "add_labels=agent-ready",
                            "-f", "remove_labels=needs-triage"]]


def test_scoped_labels_pass_the_allow_list(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, ISSUE_1, REFINE_READY_LABEL="refine::ready", REFINE_TRIAGE_LABEL="refine::triage",
                     REFINE_ALLOWED_LABELS="refine::ready,refine::triage")
    w.edit_labels(1, ["refine::ready"], [], verdict())
    assert field(fake.writes[0], "add_labels") == "refine::ready"


def test_a_label_a_person_removed_is_not_put_back(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, {**ISSUE_1, "P/issues/1/resource_label_events": "label_events_vetoed.json"})
    result = w.edit_labels(1, ["bug"], [], verdict())
    assert result["added"] == [] and any("a person removed it" in r for r in result["refused"])
    assert fake.writes == []


def test_size_drift_withholds_the_ready_label(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, ISSUE_1, REFINE_MAX_POINTS="1")
    result = w.edit_labels(1, ["agent-ready"], [], verdict(size_points=1))
    assert any("size now reads 2" in r for r in result["refused"]) and fake.writes == []


def test_comment_text_cannot_run_a_quick_action(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, {**ISSUE_1, "P/issues/1/notes": "notes_2.json"})
    assert w.write_size_marker(1, 2, "/close it /label ~x", verdict(unsized=True, size_points=None))
    (call,) = fake.writes
    assert call[:4] == ["api", "-X", "POST", f"{P}/issues/1/notes"]
    body = field(call, "body") or ""
    assert body == "<!-- refine:size 2 -->\n​/close it /label ~x"
    assert refine_write_gitlab.neutralise_quick_actions("a\n  /close\n/x") == "a\n  ​/close\n​/x"


def test_exact_duplicate_is_closed_under_the_cap(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, {**ISSUE_1, **ISSUE_3, "P/events": "project_events_2_closes.json"})
    w.close_duplicate(3, 1, verdict())
    note, close = fake.writes
    assert note[:4] == ["api", "-X", "POST", f"{P}/issues/3/notes"] and "#1" in (field(note, "body") or "")
    assert close == ["api", "-X", "PUT", f"{P}/issues/3", "-f", "state_event=close"]
    assert w.bot_closes_in_window() == 2, "the bot's closes in 7 days; not a person's, not older ones"


def test_close_cap_reached_refuses(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, {**ISSUE_1, **ISSUE_3, "P/events": "project_events_10_closes.json"})
    with pytest.raises(Refused, match="close cap"):
        w.close_duplicate(3, 1, verdict())
    assert fake.writes == []


def test_a_reopened_issue_is_not_closed_again(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, {**ISSUE_1, **ISSUE_3, "P/issues/3/resource_state_events": "state_events_reopened.json"})
    with pytest.raises(Refused, match="reopened"):
        w.close_duplicate(3, 1, verdict())
    assert fake.writes == []


def test_blocked_by_edge_is_a_blocks_link(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, {**ISSUE_1, **ISSUE_3, "P/issues/3/links": "links_none.json",
                                "P/issues/1/links": "links_none.json"})
    assert w.add_blocked_by(3, 1)
    assert fake.writes == [["api", "-X", "POST", f"{P}/issues/3/links", "-f", "target_project_id=42",
                            "-f", "target_issue_iid=1", "-f", "link_type=is_blocked_by"]]


def test_blocked_by_edge_already_there_or_a_cycle(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, {**ISSUE_1, **ISSUE_3, "P/issues/3/links": "links_3_blocks_1.json"})
    assert w.add_blocked_by(3, 1) is False
    cycle = json.dumps([{"iid": 3, "project_id": 42, "link_type": "is_blocked_by"}])
    w, fake = writer(tmp_path, {**ISSUE_1, **ISSUE_3, "P/issues/3/links": "links_none.json", "P/issues/1/links": cycle})
    with pytest.raises(Refused, match="cycle"):
        w.add_blocked_by(3, 1)
    assert fake.writes == []


def test_dry_run_sends_nothing_and_logs_the_write(tmp_path: Path) -> None:
    w, fake = writer(tmp_path, ISSUE_1, live=False)
    w.edit_labels(1, ["agent-ready"], [], verdict())
    assert fake.writes == []
    log = [json.loads(line) for line in (tmp_path / "state" / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["mode"] == "dry-run" and log[0]["action"] == "edit-labels"


def test_a_refiner_close_reads_as_duplicate(tmp_path: Path) -> None:
    w, _ = writer(tmp_path, {**ISSUE_1, "P/issues/8": "w_issue_8_closed.json", "P/issues/8/notes": "notes_close_marker.json"})
    info = w.issue(8)
    assert info["state"] == "closed" and info["state_reason"] == "duplicate" and info["closed_by"]["login"] == BOT
    assert w.issue(1)["state_reason"] is None


def test_make_writer_refuses_an_unknown_platform(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="github or gitlab"):
        refine_write.make_writer(refine_write.Settings(env(tmp_path, REFINE_PLATFORM="bitbucket")), FakeGlab({}))
    assert type(refine_write.make_writer(refine_write.Settings({"GH_REPO": "a/b"}))) is refine_write.Writer


def test_apply_settled_runs_on_gitlab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in env(tmp_path).items():
        monkeypatch.setenv(key, value)
    for key in ("GH_REPO", "GITHUB_REPOSITORY", "REFINE_BOT_LOGIN"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "verdict.json"
    path.write_text(json.dumps(verdict()), encoding="utf-8")
    fake = FakeGlab(ISSUE_1)
    assert refine_apply.main(["settled", "--verdict", str(path), "--issue", "1"], runner=fake) == 0
    assert field(fake.writes[0], "add_labels") == "agent-ready"
