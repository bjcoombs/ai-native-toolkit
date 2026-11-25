"""Tests for the refinement script's read-only platform access and definition parser (issue #1320)."""
import subprocess

import pytest

from refine_env import FIXTURE_REPO

import refine_definition  # noqa: E402  (refine_env put the scripts on sys.path)
import refine_platform  # noqa: E402


@pytest.mark.parametrize("args", [
    ["issue", "list", "--repo", "a/b"],
    ["pr", "list", "--json", "files"],
    ["issue", "view", "1"],
    ["api", "repos/a/b/issues/1/dependencies/blocked_by"],
    ["api", "--paginate", "repos/a/b/issues"],
])
def test_read_calls_pass(args):
    refine_platform.check_read_only(args)


@pytest.mark.parametrize("args", [
    ["api", "repos/a/b/issues/1", "-X", "PATCH"],
    ["api", "repos/a/b/issues/1", "-XPATCH"],
    ["api", "repos/a/b/issues/1", "--method=DELETE"],
    ["api", "repos/a/b/issues/1/labels", "-f", "labels[]=x"],
    ["api", "repos/a/b/issues/1/labels", "-Fissue_id=3"],
    ["api", "graphql", "--raw-field", "query=mutation{}"],
    ["api", "repos/a/b/issues", "--input", "body.json"],
    ["issue", "create", "--title", "x"],
    ["label", "create", "x"],
    ["pr", "merge", "1"],
    [],
])
def test_write_capable_calls_are_refused(args):
    with pytest.raises(refine_platform.ReadOnlyViolation):
        refine_platform.check_read_only(args)


def test_refused_call_never_starts_a_process(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess started")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(refine_platform.ReadOnlyViolation):
        refine_platform.run_gh_readonly(["issue", "lock", "1"])


def test_runner_returns_stdout_and_raises_on_failure(monkeypatch):
    seen = []

    def fake(cmd, **kwargs):
        seen.append((cmd, kwargs))
        code = 0 if cmd[2] == "list" else 1
        return subprocess.CompletedProcess(cmd, code, stdout="[]", stderr="HTTP 401")

    monkeypatch.setattr(subprocess, "run", fake)
    assert refine_platform.run_gh_readonly(["issue", "list"]) == "[]"
    assert seen[0][0] == ["gh", "issue", "list"]
    assert seen[0][1]["encoding"] == "utf-8"
    with pytest.raises(refine_platform.GhError, match="HTTP 401"):
        refine_platform.run_gh_readonly(["issue", "view", "1"])


def test_github_issue_maps_the_graphql_shapes():
    issue = refine_platform.github_issue({
        "number": 7, "title": "t", "body": None, "state": "OPEN", "stateReason": "NOT_PLANNED",
        "labels": [{"name": "b"}, {"name": "a"}], "createdAt": "2026-11-01T00:00:00Z",
        "url": "u", "blockedBy": {"nodes": [{"number": 3, "state": "CLOSED"}], "totalCount": 1},
        "subIssuesSummary": {"total": 2}, "comments": [{"body": "c", "createdAt": "2026-11-02T00:00:00Z"}],
    })
    assert issue == {
        "number": 7, "title": "t", "body": "", "state": "open", "state_reason": "not_planned",
        "labels": ["a", "b"], "created_at": "2026-11-01T00:00:00Z", "closed_at": None, "url": "u",
        "blocked_by": [{"number": 3, "state": "closed"}], "sub_issues_total": 2,
        "comments": [{"body": "c", "created_at": "2026-11-02T00:00:00Z"}],
    }


def test_closed_list_at_the_search_cap_is_marked_truncated():
    rows = '[' + ",".join('{"number": %d}' % n for n in range(1, refine_platform.LIST_LIMIT + 1)) + ']'
    backlog = refine_platform.GitHubBacklog(FIXTURE_REPO, runner=lambda args: rows)
    from datetime import date
    assert len(backlog.closed_issues_since(date(2026, 1, 1))) == refine_platform.LIST_LIMIT
    assert backlog.closed_truncated is True


def test_open_list_at_the_cap_is_marked_truncated():
    rows = '[' + ",".join('{"number": %d}' % n for n in range(1, refine_platform.LIST_LIMIT + 1)) + ']'
    backlog = refine_platform.GitHubBacklog(FIXTURE_REPO, runner=lambda args: rows)
    assert backlog.open_truncated is False
    backlog.open_issues()
    assert backlog.open_truncated is True


def test_non_array_output_is_an_error():
    backlog = refine_platform.GitHubBacklog(FIXTURE_REPO, runner=lambda args: "{}")
    with pytest.raises(refine_platform.GhError):
        backlog.open_pull_requests()


# --- YAML subset ---------------------------------------------------------------

def test_yaml_subset_shapes():
    text = "a: 1\nb: [2, 3]\nc:\n  - x\n  - 'y: z'\nd:\n  e: \"f\"\ng:\n  - id: h\n    check: script\n# note\nk:\n"
    assert refine_definition.parse_yaml_subset(text) == {
        "a": 1, "b": [2, 3], "c": ["x", "y: z"], "d": {"e": "f"},
        "g": [{"id": "h", "check": "script"}], "k": None,
    }


@pytest.mark.parametrize("text", ["a: {b: 1}", "a: 1\n   b: 2", "- a\nb: 1", "just text", "", "a: &x 1"])
def test_yaml_outside_the_subset_is_refused(text):
    with pytest.raises(refine_definition.DefinitionError):
        refine_definition.parse_yaml_subset(text)


def test_document_needs_exactly_one_yaml_block():
    with pytest.raises(refine_definition.DefinitionError, match="found 2"):
        refine_definition.extract_block("```yaml\na: 1\n```\n```yaml\nb: 2\n```\n")
    with pytest.raises(refine_definition.DefinitionError, match="found 0"):
        refine_definition.extract_block("no block\n")


BLOCK = """version: 1
sections: [A]
heading_levels: [2]
empty_markers: []
max_points: 3
size_pattern: 'Size: (\\d+)'
size_marker: '<!-- refine:size (\\d+) -->'
labels:
  triage: needs-triage
  ready: agent-ready
checklist:
  - id: sized
    check: script
"""


def test_valid_block_loads():
    assert refine_definition.definition_from_block(BLOCK)["max_points"] == 3


@pytest.mark.parametrize("old,new,match", [
    ("max_points: 3", "max_points: three", "max_points"),
    ("  ready: agent-ready\n", "", "ready"),
    ("check: script", "check: maybe", "script or judgement"),
    ("heading_levels: [2]", "heading_levels: [two]", "heading_levels"),
    ("sections: [A]", "sections: [1]", "sections"),
    ("size_pattern: 'Size: (\\d+)'", "size_pattern: '('", "size_pattern"),
    ("size_marker: '<!-- refine:size (\\d+) -->'", "size_marker: 'no group'", "size_marker"),
])
def test_malformed_block_is_refused(old, new, match):
    with pytest.raises(refine_definition.DefinitionError, match=match):
        refine_definition.definition_from_block(BLOCK.replace(old, new))
