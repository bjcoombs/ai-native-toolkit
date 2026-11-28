"""Merge and review rules kept as code, as evidence on the review condition (issue #856):
``lib.ci.review_rules``, ``lib.ci.yaml_tree`` and the ``review_rules`` entry of
``lib.scoring.condition_evidence``."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.ci.review_rules as rr
import lib.scoring.level_tally as lt
from lib.ci.review_rules import committed_merge_queue, rules_or_unknown, scan_review_rules
from lib.ci.yaml_tree import parse
from lib.counts_only.content_free import counts_only
from lib.pack_yaml import YamlError

REVIEW = "layer-7-review.1"

POLICY = """\
policy:
  approval:
    - or:
      - two maintainers
      - docs only
approval_rules:
  - name: two maintainers
    requires:
      count: 2
      teams: ["acme/maintainers"]
  - name: docs only
    if:
      only_changed_files:
        paths: ["^docs/.*$"]
"""
SUB_OWNERS = "approvers:\n  - alice\n  - bob\nreviewers: [carol]\n"
ROOT_OWNERS = "# See the OWNERS docs\napprovers:\n- alice\n"
MERGE_GROUP = "name: ci\non:\n  pull_request:\n  merge_group:\njobs:\n  t:\n    runs-on: x\n"


def _git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _repo(root: Path, files: dict[str, str], untracked: dict[str, str] | None = None) -> Path:
    _git(root, "init", "-q")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", ".")
    for rel, text in (untracked or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _mkdir(path: Path) -> Path:
    path.mkdir()
    return path


def _rules(root: Path) -> list[tuple[str, str]]:
    return [(r["arm"], r["path"]) for r in scan_review_rules(root)["rules"]]


def _workflow(on: str, run: str) -> str:
    return f"name: w\non: {on}\njobs:\n  r:\n    runs-on: x\n    steps:\n      - run: {run}\n"


def _ruleset(**over: Any) -> str:
    doc: dict[str, Any] = {"name": "main", "target": "branch", "enforcement": "active",
                           "rules": [{"type": "pull_request",
                                      "parameters": {"required_approving_review_count": 1}}]}
    doc.update(over)
    return json.dumps(doc)


# --- criterion 1: OWNERS plus .policy.yml counts as review evidence ---

def test_owners_and_a_policy_bot_config_count_as_review_evidence(tmp_path: Path) -> None:
    block = scan_review_rules(_repo(tmp_path, {".policy.yml": POLICY, "src/OWNERS": SUB_OWNERS,
                                               "OWNERS": ROOT_OWNERS}))
    assert block["present"] is True
    assert block["arms"] == ["policy_bot", "prow_owners"]
    assert block["rules"] == [{"arm": "policy_bot", "path": ".policy.yml"},
                              {"arm": "prow_owners", "path": "OWNERS"},
                              {"arm": "prow_owners", "path": "src/OWNERS"}]
    assert block["catch_all_owners"] is False


# --- criterion 2: a merge_group workflow counts ---

def test_a_merge_group_workflow_and_a_mergify_queue_count_as_merge_queues(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": MERGE_GROUP,
                            ".mergify.yml": "queue_rules:\n  - name: default\n    conditions: []\n",
                            ".github/workflows/lint.yml": _workflow("[push, pull_request]", "ruff check")})
    assert _rules(root) == [("merge_queue", ".github/workflows/ci.yml"), ("merge_queue", ".mergify.yml")]
    assert committed_merge_queue(scan_review_rules(root)) is True


# --- criterion 3: a top-level OWNERS with one owner for everything does not count ---

@pytest.mark.parametrize("files", [
    {"OWNERS": ROOT_OWNERS},
    {"OWNERS": "approvers: [alice]\nreviewers: [Alice]\n"},
    {"OWNERS": ROOT_OWNERS, "src/OWNERS": "approvers:\n  - alice\n"},
    {"OWNERS": "filters:\n  \".*\":\n    approvers: [alice]\n"},
    {"OWNERS": "approvers: [leads]\n", "OWNERS_ALIASES": "aliases:\n  leads:\n    - alice\n"},
])
def test_one_owner_for_everything_is_a_catch_all(tmp_path: Path, files: dict[str, str]) -> None:
    block = scan_review_rules(_repo(tmp_path, files))
    assert block["arms"] == [] and block["present"] is False
    assert block["catch_all_owners"] is True


def test_an_alias_naming_two_people_is_not_a_catch_all(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"OWNERS": "approvers: [leads]\n",
                            "OWNERS_ALIASES": "aliases:\n  leads: [alice, bob]\n"})
    assert _rules(root) == [("prow_owners", "OWNERS")]


def test_an_unread_owners_file_never_reads_catch_all(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"OWNERS": ROOT_OWNERS, "src/OWNERS": "approvers: &a [bob]\n"})
    block = scan_review_rules(root)
    assert block["catch_all_owners"] is False
    assert block["arms"] == [] and block["present"] is None
    assert block["unsettled"] == ["src/OWNERS"]


# --- false-met traps ---

@pytest.mark.parametrize("text", [
    POLICY.replace("count: 2", "count: 0"),
    "policy:\n  approval: []\napproval_rules:\n  - name: x\n    requires:\n      count: 1\n",
    "approval_rules:\n  - name: x\n    requires:\n      count: 1\n",
    "policy:\n  approval: [x]\napproval_rules:\n  - name: x\n    requires:\n      count: \"1\"\n",
    "policy:\n  approval: [x]\napproval_rules:\n  - name: x\n    requires:\n      count: true\n",
    "# approval_rules:\n#   - requires:\n#       count: 1\npolicy: {}\n",
    # an unreferenced rule that needs approval, beside an auto-approve that is in force
    "policy:\n  approval: [auto]\napproval_rules:\n  - name: auto\n  - name: unused\n    requires:\n"
    "      count: 2\n",
    # a lone conditional rule: changes it does not match need no approval
    "policy:\n  approval: [docs-review]\napproval_rules:\n  - name: docs-review\n    if:\n"
    "      changed_files:\n        paths: [\"^docs/\"]\n    requires: {count: 1}\n",
    # an or with an unconditional auto-approve branch
    "policy:\n  approval:\n    - or: [auto, manual]\napproval_rules:\n  - name: auto\n  - name: manual\n"
    "    requires: {count: 1}\n",
])
def test_a_policy_that_requires_no_approval_does_not_count(tmp_path: Path, text: str) -> None:
    block = scan_review_rules(_repo(tmp_path, {".policy.yml": text}))
    assert block["arms"] == [] and block["present"] is False


def test_an_and_of_an_auto_approve_and_a_required_rule_counts(tmp_path: Path) -> None:
    text = ("policy:\n  approval:\n    - and: [auto, manual]\napproval_rules:\n  - name: auto\n"
            "  - name: manual\n    requires: {count: 1}\n")
    assert _rules(_repo(tmp_path, {".policy.yml": text})) == [("policy_bot", ".policy.yml")]


def test_a_policy_naming_an_undefined_rule_is_unsettled(tmp_path: Path) -> None:
    text = "policy:\n  approval: [missing]\napproval_rules:\n  - name: x\n    requires: {count: 1}\n"
    block = scan_review_rules(_repo(tmp_path, {".policy.yml": text}))
    assert block["present"] is None and block["unsettled"] == [".policy.yml"]


@pytest.mark.parametrize(("files", "expected", "unsettled"), [
    ({".github/rulesets/main.json": _ruleset()}, ["ruleset_json"], []),
    ({".github/rulesets/main.json": _ruleset(rules=[{"type": "merge_queue", "parameters": {}}])},
     ["ruleset_json"], []),
    ({".github/rulesets/main.json": _ruleset(enforcement="evaluate")}, [], []),
    ({".github/rulesets/main.json": _ruleset(enforcement="disabled")}, [], []),
    ({".github/rulesets/main.json": _ruleset(target="tag")}, [], []),
    ({".github/rulesets/main.json": _ruleset(rules=[
        {"type": "pull_request", "parameters": {"required_approving_review_count": 0}}])}, [], []),
    ({".github/rulesets/main.json": _ruleset(enforcement=None)}, [], [".github/rulesets/main.json"]),
    ({".github/rulesets/main.json": '{"name": "main", "rules": ['}, [], [".github/rulesets/main.json"]),
    ({".github/protection/main.json": json.dumps(
        {"required_pull_request_reviews": {"required_approving_review_count": 2}})}, ["ruleset_json"], []),
    ({".github/protection/main.json": json.dumps({"enforce_admins": {"enabled": True}})}, [], []),
])
def test_a_ruleset_counts_only_when_active_and_requiring_review(
        tmp_path: Path, files: dict[str, str], expected: list[str], unsettled: list[str]) -> None:
    block = scan_review_rules(_repo(tmp_path, files))
    assert block["arms"] == expected
    assert block["unsettled"] == unsettled
    assert block["present"] is (True if expected else None if unsettled else False)


@pytest.mark.parametrize(("files", "expected"), [
    ({".github/review-rubrics/api.md": "x", ".github/workflows/r.yml":
      _workflow("pull_request", "review --rubric .github/review-rubrics/api.md")},
     [("review_guide", ".github/review-rubrics")]),
    ({"docs/rubrics/a.md": "x", ".github/workflows/r.yml": _workflow("pull_request", "cat ./docs/rubrics/*")},
     [("review_guide", "docs/rubrics")]),
    ({"docs/rubrics/a.md": "x", ".github/workflows/r.yml":
      _workflow("pull_request", "echo hi") + "# reads docs/rubrics\n"}, []),
    ({"docs/rubrics/a.md": "x", ".github/workflows/r.yml": _workflow("schedule", "cat docs/rubrics/a.md")}, []),
    ({"docs/rubrics/a.md": "x", ".github/workflows/r.yml": _workflow("pull_request", "cat docs/rubrics-old/a")}, []),
    ({"docs/guides/a.md": "x", ".github/workflows/r.yml": _workflow("pull_request", "cat docs/guides/a.md")}, []),
    # a paths filter says when the workflow runs, not that it uses the folder
    ({"docs/rubrics/a.md": "x", ".github/workflows/r.yml":
      "name: d\non:\n  pull_request:\n    paths: ['docs/rubrics/**']\njobs:\n  b:\n    runs-on: x\n"
      "    steps:\n      - run: mkdocs build\n"}, []),
])
def test_a_review_guide_directory_counts_when_a_pull_request_workflow_names_it(
        tmp_path: Path, files: dict[str, str], expected: list[tuple[str, str]]) -> None:
    assert _rules(_repo(tmp_path, files)) == expected


@pytest.mark.parametrize("files", [
    {"vendor/k8s.io/api/OWNERS": SUB_OWNERS},
    {"third_party/x/.github/rulesets/a.json": _ruleset()},
    {".mergify.yml": "queue_rules: []\n"},
    {".github/workflows/ci.yml": _workflow("[push]", "make") + "# merge_group:\n"},
])
def test_vendored_empty_or_commented_rules_read_nothing(tmp_path: Path, files: dict[str, str]) -> None:
    block = scan_review_rules(_repo(tmp_path, files))
    assert block["arms"] == [] and block["present"] is False


def test_an_untracked_rule_file_is_not_read(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"README.md": "x"}, untracked={".policy.yml": POLICY, "src/OWNERS": SUB_OWNERS})
    assert scan_review_rules(root)["present"] is False


def test_an_unparseable_config_reads_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".policy.yml": "policy: &p\n  approval: [x]\n",
                            ".github/workflows/x.yml": "name: x\non: [push\njobs: {}\n"})
    block = scan_review_rules(root)
    assert block["present"] is None
    assert block["unsettled"] == [".github/workflows/x.yml", ".policy.yml"]


def test_outside_a_git_checkout_the_block_is_unavailable(tmp_path: Path) -> None:
    block = scan_review_rules(tmp_path)
    assert block["available"] is False and block["present"] is None
    assert block["reason"].startswith("git_tracking_unknown")
    assert committed_merge_queue(block) is None


def test_an_unsettled_file_leaves_the_committed_merge_queue_unknown(tmp_path: Path) -> None:
    block = scan_review_rules(_repo(tmp_path, {".mergify.yml": "queue_rules: &q [x]\n"}))
    assert committed_merge_queue(block) is None
    assert committed_merge_queue(scan_review_rules(_repo(_mkdir(tmp_path / "b"), {"README.md": "x"}))) is False


def test_a_reader_fault_reads_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> None:
        raise OSError("x")
    monkeypatch.setattr(rr, "tracked_files", boom)
    assert rules_or_unknown(tmp_path) == {"available": False, "reason": "scan_failed", "present": None,
                                          "arms": [], "rules": [], "catch_all_owners": False,
                                          "unsettled": []}


# --- evidence on the review condition ---

def _level(rules: Any, review: bool | None) -> dict[str, Any]:
    data: dict[str, Any] = {"review_evidence": {"available": review is not None, "present": review}}
    if rules is not None:
        data["review_rules"] = rules
    return dict(lt.level_result(lt.default_model(), data))


def _cond(level: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(c for c in level["conditions"] if c["id"] == cid)


def test_the_arms_attach_to_a_met_review_condition_and_move_no_tally(tmp_path: Path) -> None:
    block = scan_review_rules(_repo(tmp_path, {".policy.yml": POLICY, ".github/workflows/ci.yml": MERGE_GROUP}))
    with_rules, without = _level(block, True), _level(None, True)
    assert _cond(with_rules, REVIEW)["evidence"] == ["merge_queue", "policy_bot"]
    for key in ("verified", "next_step", "tally"):
        assert with_rules.get(key) == without.get(key)
    assert _cond(with_rules, REVIEW)["state"] == _cond(without, REVIEW)["state"] == "met"


@pytest.mark.parametrize("review", [False, None])
def test_with_the_review_condition_not_met_no_evidence_attaches(tmp_path: Path, review: bool | None) -> None:
    block = scan_review_rules(_repo(tmp_path, {".policy.yml": POLICY}))
    assert "evidence" not in _cond(_level(block, review), REVIEW)


# --- counts-only ---

def test_counts_only_keeps_every_field(tmp_path: Path) -> None:
    block = scan_review_rules(_repo(tmp_path, {".policy.yml": POLICY, "src/OWNERS": SUB_OWNERS,
                                               ".github/rulesets/a.json": _ruleset(enforcement=None)}))
    kept = counts_only({"review_rules": block})["review_rules"]
    assert kept == dict(block)
    level = counts_only({"level": _level(block, True)})["level"]
    assert _cond(level, REVIEW)["evidence"] == ["policy_bot", "prow_owners"]


# --- the YAML reader ---

def test_the_yaml_reader_reads_the_subset_the_rule_files_use() -> None:
    assert parse("---\na:\n- x\n- 'y z'\nb: {count: 2, ok: true, n: ~}\nc:\n  - name: r\n    requires:\n"
                 "      count: 1\n  - [p, \"q\"]\nd: |\n  text: here\ne:\n") == {
        "a": ["x", "y z"], "b": {"count": 2, "ok": True, "n": None},
        "c": [{"name": "r", "requires": {"count": 1}}, ["p", "q"]], "d": "", "e": None}
    assert parse("# only a comment\n") is None
    assert parse("[a, b]\n") == ["a", "b"]
    assert parse("- a\n- b\n") == ["a", "b"]


@pytest.mark.parametrize("text", [
    "a: &x 1\n", "a: *x\n", "a: !tag 1\n", "<<: {}\n", "a: [1,\n  2]\n", "a: plain\n  more\n",
    "a: 1\n---\nb: 2\n", "%YAML 1.2\na: 1\n", "a: 1\na: 2\n", "a:\n\t- x\n", "a: 1\n  b: 2\n",
    "  a: 1\n", "a: 'open\n", "- a\nb: 1\n", "a: {k: v, k: w}\n", "a: [x]]\n",
    "a:\n  - - x\n", "---\n---\nqueue_rules: [default]\n",
])
def test_the_yaml_reader_fails_closed(text: str) -> None:
    with pytest.raises(YamlError):
        parse(text)
