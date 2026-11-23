"""One `on:` reader for both workflow readers, checked against a YAML parse (issue #1036)."""
from __future__ import annotations

import pytest
import yaml

from lib import ci_triggers, shared_tooling
from lib.review_automation import runs_on_changes

_JOB = "jobs:\n  a:\n    runs-on: x\n    steps:\n      - run: pytest\n"

_SHAPES = [
    "on: push\n",
    "on: [push, pull_request]\n",
    "on: {issue_comment: {types: [created]}, push: {}}\n",
    "on:\n  - push\n  - merge_group\n",
    "on:\n- push\n- pull_request\n",
    "on:\n- workflow_dispatch\n",
    "on:\n\n# c\n- push  # x\n\n- 'pull_request'\n",
    '"on":\n- push\n',
    "true:\n- pull_request\n",
    "'on':\n  issues:\n    types: [opened]\n  issue_comment:\n",
    "on:\n  push:\n    branches:\n      - main\n  workflow_dispatch:\n",
    "on:\n  issue_comment:\n    types:\n      - created\n",
    "on:\n  schedule:\n    - cron: '0 0 * * *'\n",
    "on: {\n  issue_comment: {}\n}\n",
    "on: [\n  issue_comment,  # c\n  issues\n]\n",
    "on: [workflow_dispatch,\n  push]\n",
    "name: x\n",
]


def _parsed(text: str) -> frozenset[str] | None:
    doc = next(iter(yaml.safe_load_all(text)), None) or {}
    on = doc.get("on", doc.get(True))
    if on is None:
        return None
    return frozenset([on] if isinstance(on, str) else on) or None


@pytest.mark.parametrize("on", _SHAPES)
def test_both_readers_match_a_yaml_parse(on: str) -> None:
    text = on + _JOB
    events = ci_triggers.workflow_events(text)
    assert (frozenset(events) if events else None) == _parsed(text)
    assert shared_tooling.workflow_events(text) == _parsed(text)


def test_a_document_separator_ends_the_on_list() -> None:
    text = "on:\n- push\n---\n" + _JOB
    assert ci_triggers.workflow_events(text) == ["push"]
    assert shared_tooling.workflow_events(text) == frozenset({"push"})


def test_an_unclosed_flow_list_is_not_read() -> None:
    assert ci_triggers.workflow_events("on: [push,\n") is None
    assert shared_tooling.workflow_events("on: [push,\n") is None


@pytest.mark.parametrize(("on", "credited"), [
    ("on:\n- pull_request\n", True),
    # Unreadable used to be credited; a column-0 list is now read (#1036).
    ("on:\n- issue_comment\n", False),
    ("on:\n- workflow_dispatch\n", False),
    ("on: {\n  issue_comment: {}\n}\n", False),
    ("on: [issue_comment,\n  pull_request]\n", True),
])
def test_a_review_action_runs_on_changes_from_the_shared_reader(on: str, credited: bool) -> None:
    assert runs_on_changes(on + _JOB) is credited


def test_a_flow_list_across_lines_is_ordinary() -> None:
    assert ci_triggers.workflow_trigger("on: [workflow_dispatch,\n  push]\n" + _JOB) == "ordinary"
