"""The size limit the write scripts enforce is the definition of ready's `max_points` (#1439 follow-up).

`refine.py` sizes an issue against `max_points` in the definition's fenced
yaml block. The writer must refuse the ready label at the same limit, on
GitHub and GitLab alike, unless `REFINE_MAX_POINTS` (the triage workflow's
`max_points` input, the GitLab template's variable) is set above 0.
"""
import json
import re
from pathlib import Path
from typing import Dict

import pytest
import yaml  # required: the plugin contract job installs pyyaml
from refine_gitlab_env import PROJECT, FakeGlab
from refine_write_env import REPO, writer

import refine_definition  # noqa: E402  (the env modules put the scripts on sys.path)
import refine_write  # noqa: E402
import refine_write_gitlab  # noqa: E402

DEFINITION = REPO / "docs" / "contributing" / "definition-of-ready.md"
OPEN_101 = {"issues/101": "issue_101_open.json", "issues/101/events": "events_bot_only.json"}


def _definition_saying(tmp: Path, points: int) -> None:
    """An adopter checkout whose definition of ready sets `max_points: points`."""
    text = DEFINITION.read_text(encoding="utf-8")
    text, n = re.subn(r"(?m)^max_points: \d+$", f"max_points: {points}", text)
    assert n == 1
    local = tmp / "docs" / "contributing" / "definition-of-ready.md"
    local.parent.mkdir(parents=True)
    local.write_text(text, encoding="utf-8")


def _sized(points: int) -> str:
    return json.dumps([{"id": 9, "user": {"login": "maintainer", "type": "User"}, "author_association": "OWNER",
                        "body": f"<!-- refine:size {points} -->\nsized", "created_at": "2026-11-25T11:00:00Z"}])


def _plan(size: int) -> Dict[str, object]:
    return {"issue": 101, "ready": True, "labels_add": [], "labels_remove": [], "size": size,
            "size_reason": "one module", "blocked_by": [], "questions": [], "summary": ""}


def _verdict() -> Dict[str, object]:
    data: Dict[str, object] = json.loads(
        (REPO / "tests" / "fixtures" / "refine_write" / "verdict.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("override", [None, "", "0"])
def test_a_definition_of_3_refuses_5_when_the_input_is_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              override: object) -> None:
    _definition_saying(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    kw = {} if override is None else {"REFINE_MAX_POINTS": override}
    w, gh = writer(tmp_path, {**OPEN_101, "issues/101/comments": _sized(5)}, **kw)
    assert w.s.max_points == 3
    result = w.edit_labels(101, ["agent-ready"], [], _verdict(), _plan(5))
    assert any("not between 1 and 3" in r for r in result["refused"])
    assert gh.writes == []


def test_an_explicit_input_overrides_the_definition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _definition_saying(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    w, gh = writer(tmp_path, {**OPEN_101, "issues/101/comments": _sized(5)}, REFINE_MAX_POINTS="5")
    assert w.s.max_points == 5
    w.edit_labels(101, ["agent-ready"], [], _verdict(), _plan(5))
    assert gh.writes[0][-4:] == ["--add-label", "agent-ready", "--remove-label", "needs-triage"]


GITLAB_ISSUE_1 = {"P/issues/1": "w_issue_1.json", "P/issues/1/resource_label_events": "label_events_none.json",
                  "P/issues/1/resource_state_events": "state_events_none.json", "P/issues/1/notes": "notes_1.json",
                  "P/members/all": "members.json", "user": "user_bot.json", "P": "project.json"}


@pytest.mark.parametrize("override, expected", [("", 1), ("0", 1), ("13", 13)])
def test_gitlab_reads_the_same_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     override: str, expected: int) -> None:
    _definition_saying(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    env = {"REFINE_PLATFORM": "gitlab", "CI_PROJECT_PATH": PROJECT, "REFINE_MODE": "live",
           "REFINE_MAX_POINTS": override, "REFINE_STATE_DIR": str(tmp_path / "state")}
    fake = FakeGlab(GITLAB_ISSUE_1)
    w = refine_write.make_writer(refine_write.Settings(env), fake)
    assert isinstance(w, refine_write_gitlab.GitLabWriter)
    assert w.s.max_points == expected
    assert refine_write.Settings({**env, "REFINE_PLATFORM": "github", "GH_REPO": "a/b"}).max_points == expected
    if expected == 1:  # the issue's notes size it at 2, above the definition's 1
        issue = {"number": 1, "verdict": "ready", "settled": True, "checklist": [], "overlaps": [],
                 "duplicates": [], "unsized": False, "size_points": 1, "labels": ["needs-triage"]}
        result = w.edit_labels(1, ["agent-ready"], [], {"definition": {}, "issues": [issue], "duplicates": []})
        assert any("size now reads 2, above 1" in r for r in result["refused"]) and fake.writes == []


def test_no_definition_anywhere_falls_back_to_the_toolkit_default(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    # A write job checks out only the scripts, so with no definition in the
    # adopter's repository neither place has the document.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(refine_definition, "TOOLKIT_ROOT", tmp_path / "scripts-only")
    assert refine_definition.size_limit(0, 99) == 99


@pytest.mark.parametrize("raw", ["-1", "eight", "3.5"])
def test_a_malformed_override_fails_before_any_write(raw: str) -> None:
    # Settings is built before the first write, so a bad value stops the job
    # there rather than part-way through an issue (size marker written, no label).
    with pytest.raises(refine_definition.DefinitionError, match="REFINE_MAX_POINTS"):
        refine_write.Settings({"GH_REPO": "a/b", "REFINE_MAX_POINTS": raw})


@pytest.mark.parametrize("raw, value", [("", 0), (" 0 ", 0), ("13", 13)])
def test_parse_override(raw: str, value: int) -> None:
    assert refine_definition.parse_override(raw) == value


def test_the_limit_is_read_once_per_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _definition_saying(tmp_path, 3)
    monkeypatch.chdir(tmp_path)
    s = refine_write.Settings({"GH_REPO": "a/b"})
    assert s.max_points == 3
    _definition_saying(tmp_path / "moved", 5)
    (tmp_path / "moved" / "docs").replace(tmp_path / "docs-new")
    (tmp_path / "docs").rename(tmp_path / "docs-old")
    (tmp_path / "docs-new").rename(tmp_path / "docs")
    assert s.max_points == 3


def test_the_four_defaults_agree() -> None:
    """The writer's fallback, the shipped definition, and the GitHub and GitLab defaults are one value.

    The workflow input and the GitLab variable default to 0, which defers to
    the definition, so the effective limit of each is the definition's.
    """
    definition = refine_definition.load_definition(DEFINITION)["max_points"]
    triage = yaml.safe_load((REPO / ".github" / "workflows" / "reusable-refine-triage.yml").read_text(encoding="utf-8"))
    workflow_default = triage[True]["workflow_call"]["inputs"]["max_points"]["default"]
    _, jobs = yaml.safe_load_all((REPO / "templates" / "refine.yml").read_text(encoding="utf-8"))
    template_default = jobs[".refine-base"]["variables"]["REFINE_MAX_POINTS"]
    fallback = refine_write.DEFAULT_MAX_POINTS
    effective = {
        "refine_write.DEFAULT_MAX_POINTS": fallback,
        "definition-of-ready.md max_points": definition,
        "reusable-refine-triage.yml max_points": refine_definition.size_limit(refine_definition.parse_override(str(workflow_default)), fallback),
        "templates/refine.yml REFINE_MAX_POINTS": refine_definition.size_limit(refine_definition.parse_override(str(template_default)), fallback),
    }
    assert len(set(effective.values())) == 1, effective
