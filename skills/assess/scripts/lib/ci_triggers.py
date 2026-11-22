"""Does a CI line run on ordinary changes? (issue #775)

``ci_checks.enforced_check_runs`` counts a check only when CI runs it on every
ordinary change, so its input class is ``ci_executed``. This module reads the
triggers, line by line with the ``ci_masking`` indentation model (no YAML
parser), and classifies each CI line as ``ordinary``, ``not_ordinary`` or
``unknown``. Literal values only.

GitHub Actions, per workflow file, from its top-level ``on`` key (``"on"`` and
the YAML 1.1 ``true`` spelling included), as a scalar, a flow list or mapping,
or a block list or mapping:

- ``push``, ``pull_request``, ``pull_request_target`` or ``merge_group`` among
  the events: ``ordinary``. Branch and path filters are not read.
- ``workflow_call`` or ``workflow_run`` and no ordinary event: ``called``; the
  scan makes it ``ordinary`` when an ordinary workflow of the same repository
  calls it (``uses: ./.github/workflows/<file>``), else ``unknown``.
- Only other events (``workflow_dispatch``, ``schedule``, ``release``,
  ``issue_comment`` ...): ``not_ordinary``.
- No ``on`` key, or one this reader cannot list: ``unknown``.

GitLab CI, per job (a top-level key of the file):

- ``when: manual`` or ``when: never`` on the job: ``not_ordinary``.
- ``only:`` as a scalar or list: ``ordinary`` when it names ``branches``,
  ``merge_requests``, ``pushes`` or a branch name; ``not_ordinary`` when it names
  only ``tags``, ``schedules``, ``web``, ``triggers``, ``api``, ``pipelines``,
  ``external`` or ``chat``. The map form (``only: {refs: ...}``) reads its
  ``refs``; anything else is ``unknown``.
- ``rules:``: a rule that runs (``when`` is not ``never`` or ``manual``) whose
  ``if:`` is absent or names a merge request or branch variable
  (``CI_PIPELINE_SOURCE == "merge_request_event"`` / ``"push"``,
  ``CI_MERGE_REQUEST_*``, ``CI_COMMIT_BRANCH``, ``CI_DEFAULT_BRANCH``,
  ``CI_COMMIT_REF_NAME``, ``CI_OPEN_MERGE_REQUESTS``) makes the job
  ``ordinary``. With none, a running rule whose ``if:`` names anything else
  except a schedule, web, trigger or api source or ``CI_COMMIT_TAG`` makes it
  ``unknown``; otherwise ``not_ordinary``.
- No such key: ``ordinary``. Lines outside a job (``default:``, a top-level
  ``before_script:``) are ``ordinary``.

Known limits: rules and ``when`` inherited through ``extends:``, ``default:``
or a YAML anchor are not read, nor a top-level ``workflow: rules:``; hidden
jobs (``.name:``) are read like any job; GitHub branch and path filters and
``if:`` conditions are not read here (a literal ``if: false`` is a mask in
``lib.ci_masking``, #919). Only a workflow file directly under
``.github/workflows/`` is read: GitHub does not run one in a subfolder
(``command_index.ci_config_files``).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from lib.ci_masking import IndentTree, parse_lines, strip_comment

Trigger = Literal["ordinary", "not_ordinary", "unknown"]
WorkflowTrigger = Literal["ordinary", "not_ordinary", "unknown", "called"]

_ON = re.compile(r"""^(?:on|"on"|'on'|true)\s*:(.*)$""")
_ORDINARY_EVENTS = frozenset({"push", "pull_request", "pull_request_target", "merge_group"})
_CALL_EVENTS = frozenset({"workflow_call", "workflow_run"})
_LOCAL_CALL = re.compile(r"uses:\s*['\"]?\./\.github/workflows/([\w.-]+\.ya?ml)")
_GITLAB_RESERVED = frozenset({
    "stages", "variables", "include", "workflow", "default", "image", "services", "cache",
    "before_script", "after_script",
})
_SPECIAL_ONLY = frozenset({"tags", "schedules", "web", "triggers", "api", "pipelines", "external", "chat"})
_ORDINARY_IF = re.compile(
    r"CI_PIPELINE_SOURCE\s*==\s*['\"](?:merge_request_event|push)['\"]|CI_MERGE_REQUEST_\w+|"
    r"CI_COMMIT_BRANCH|CI_DEFAULT_BRANCH|CI_COMMIT_REF_NAME|CI_OPEN_MERGE_REQUESTS")
_SPECIAL_IF = re.compile(
    r"^\s*(?:\$CI_PIPELINE_SOURCE\s*==\s*['\"](?:schedule|web|trigger|api)['\"]|\$CI_COMMIT_TAG)\s*$")


def _names(text: str) -> list[str]:
    """Event names from an inline value: a scalar, ``[a, b]`` or ``{a: ..., b: ...}``."""
    text = text.strip()
    if text.startswith("["):
        return [w.strip(" '\"") for w in text.strip("[]").split(",") if w.strip(" '\"")]
    if text.startswith("{"):
        names: list[str] = []
        depth = 0
        word: list[str] = []
        for ch in text:
            if ch in "{[":
                depth += 1
                word = []
            elif ch in "}]":
                depth -= 1
                word = []
            elif ch == ":" and depth == 1:
                names.append("".join(word).strip(" ,'\""))
                word = []
            elif ch == ",":
                word = []
            else:
                word.append(ch)
        return [n for n in names if n]
    return [text.strip("'\"")] if text else []


def workflow_events(text: str) -> list[str] | None:
    """The events a GitHub Actions workflow's top-level ``on`` names, or None when not readable."""
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        match = _ON.match(raw)
        if match is None:
            continue
        inline = strip_comment(match.group(1))
        if inline:
            return _names(inline) or None
        events: list[str] = []
        child_indent = -1
        for line in lines[i + 1:]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0:
                break
            if child_indent < 0:
                child_indent = indent
            if indent != child_indent:
                continue
            item = stripped[2:] if stripped.startswith("- ") else stripped.split(":", 1)[0]
            events.append(strip_comment(item).strip("'\""))
        return [e for e in events if e] or None
    return None


def workflow_trigger(text: str) -> WorkflowTrigger:
    """``ordinary``, ``not_ordinary``, ``called`` or ``unknown`` for one GitHub Actions workflow."""
    events = workflow_events(text)
    if events is None:
        return "unknown"
    named = set(events)
    if named & _ORDINARY_EVENTS:
        return "ordinary"
    if named & _CALL_EVENTS:
        return "called"
    return "not_ordinary"


def local_calls(text: str) -> set[str]:
    """Workflow file names (``tests.yml``) this workflow calls as local reusable workflows."""
    return set(_LOCAL_CALL.findall(text))


def resolve_workflows(triggers: dict[str, WorkflowTrigger], texts: dict[str, str]) -> dict[str, Trigger]:
    """Settle ``called`` workflows: ``ordinary`` when an ordinary workflow calls them, else ``unknown``."""
    settled: dict[str, Trigger] = {}
    ordinary = {rel for rel, t in triggers.items() if t == "ordinary"}
    changed = True
    while changed:
        changed = False
        called = {name for rel in ordinary for name in local_calls(texts[rel])}
        for rel, trigger in triggers.items():
            if trigger == "called" and rel not in ordinary and rel.rsplit("/", 1)[-1] in called:
                ordinary.add(rel)
                changed = True
    for rel, trigger in triggers.items():
        if rel in ordinary:
            settled[rel] = "ordinary"
        elif trigger == "called":
            settled[rel] = "unknown"
        else:
            settled[rel] = "not_ordinary" if trigger == "not_ordinary" else "unknown"
    return settled


def _only_trigger(tree: IndentTree, key: int) -> Trigger:
    line = tree.lines[key]
    values: list[str]
    if line.value:
        values = _names(line.value)
    else:
        children = tree.children(key)
        refs = [k for k in children if tree.lines[k].key == "refs"]
        if refs:
            ref = tree.lines[refs[0]]
            values = _names(ref.value) if ref.value else [tree.lines[k].value for k in tree.children(refs[0])]
        elif children and all(tree.lines[k].dash and not tree.lines[k].key for k in children):
            values = [tree.lines[k].value.strip("'\"") for k in children]
        else:
            return "unknown"
    if not values:
        return "unknown"
    return "not_ordinary" if all(v in _SPECIAL_ONLY for v in values) else "ordinary"


def _rules_trigger(tree: IndentTree, key: int) -> Trigger:
    unknown = False
    for item in tree.items(key):
        fields = {tree.lines[k].key: tree.lines[k].value for k in item}
        if fields.get("when", "").strip("'\"") in ("never", "manual"):
            continue
        condition = fields.get("if")
        if condition is None or _ORDINARY_IF.search(condition):
            return "ordinary"
        bare = condition.strip()
        if len(bare) > 1 and bare[0] == bare[-1] and bare[0] in "'\"":
            bare = bare[1:-1]
        if not _SPECIAL_IF.match(bare):
            unknown = True
    return "unknown" if unknown else "not_ordinary"


def _job_trigger(tree: IndentTree, job: int) -> Trigger:
    keys = {tree.lines[k].key: k for k in tree.children(job)}
    when = tree.lines[keys["when"]].value.strip("'\"") if "when" in keys else ""
    if when in ("manual", "never"):
        return "not_ordinary"
    if "rules" in keys:
        return _rules_trigger(tree, keys["rules"])
    if "only" in keys:
        return _only_trigger(tree, keys["only"])
    return "ordinary"


def gitlab_line_triggers(text: str) -> list[Trigger]:
    """Per line of one GitLab CI file: does the job holding it run on ordinary pipelines?"""
    tree = IndentTree(parse_lines(text.splitlines()))
    jobs: dict[int, Trigger] = {}
    out: list[Trigger] = []
    for i in range(len(tree.lines)):
        ancestors = tree.ancestors(i)
        top = ancestors[-1] if ancestors else None
        if top is None or tree.lines[top].level != 0 or tree.lines[top].key in _GITLAB_RESERVED:
            out.append("ordinary")
            continue
        if top not in jobs:
            jobs[top] = _job_trigger(tree, top)
        out.append(jobs[top])
    return out


def keep(lines: Iterable[str], triggers: Iterable[Trigger], wanted: Trigger) -> list[str]:
    """``lines`` with every line whose trigger is not ``wanted`` blanked (indices kept)."""
    return [line if trigger == wanted else "" for line, trigger in zip(lines, triggers, strict=True)]
