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

GitLab CI, per job (a top-level key of the file), read for a merge request pipeline
and a branch push pipeline (``lib.gitlab_rules``); the job is ``ordinary`` when it
runs in either, ``not_ordinary`` when it runs in neither and ``unknown`` otherwise:

- ``rules:``: the first matching rule decides, with ``when: never``, ``manual`` and
  ``on_failure`` as no run, and the job-level ``when:`` as each rule's default. ``if:``
  expressions are evaluated (``&&``, ``||``, parentheses, ``!``, ``==`` / ``!=`` /
  ``=~`` / ``!~``), so ``$CI_PIPELINE_SOURCE == "schedule" && $CI_COMMIT_BRANCH ==
  $CI_DEFAULT_BRANCH`` and ``$CI_COMMIT_TAG && $CI_COMMIT_BRANCH == null`` run in
  neither (#884). A custom variable or an expression not read is ``unknown``; so is
  ``rules:`` given inline (``!reference``, an anchor) and a rule whose plain ``if:``
  continues on the next line.
- ``when: manual``, ``never`` or ``on_failure`` on a job without ``rules:``:
  ``not_ordinary``.
- ``only:`` as a scalar or list: merge requests when it names ``merge_requests``,
  branch pushes when it names ``branches``, ``pushes`` or a branch name; it names
  only ``tags``, ``schedules``, ``web``, ``triggers``, ``api``, ``pipelines``,
  ``external`` or ``chat``: neither. The map form (``only: {refs: ...}``) reads its
  ``refs``; anything else is ``unknown``.
- No such key: branch pushes, and merge requests too when ``workflow: rules:`` exists.
- A top-level ``workflow: rules:`` decides which of the two pipelines GitLab creates,
  with the same reader; a job runs only in a pipeline that is created.
- Lines outside a job (``default:``, a top-level ``before_script:``) are ``ordinary``
  when ``workflow: rules:`` (if any) creates either pipeline.
- A job limited to some branches inside a workflow limited to some branches is
  ``unknown``: the two limits are not compared.

Known limits: rules and ``when`` inherited through ``extends:``, ``default:``
or a YAML anchor are not read, nor ``except:``; ``workflow: rules:`` is read only
in the file that holds it, not across ``include:``; hidden
jobs (``.name:``) are read like any job; GitHub branch and path filters and
``if:`` conditions are not read here (a literal ``if: false`` is a mask in
``lib.ci_masking``, #919). Only a workflow file directly under
``.github/workflows/`` is read: GitHub does not run one in a subfolder
(``command_index.ci_config_files``).

Does a CI line run on ordinary changes (issue #775)? It makes
`ci_checks.enforced_check_runs` a `ci_executed` signal. `workflow_events(text)` lists a
GitHub workflow's top-level `on` events (scalar, flow list or mapping, block list or
mapping; `"on"` and YAML 1.1 `true` too), or None when it cannot; `workflow_trigger`
maps them to `ordinary` (`push`, `pull_request`, `pull_request_target`,
`merge_group`), `called` (`workflow_call` / `workflow_run` only), `not_ordinary`
(`workflow_dispatch`, `schedule` ...) or `unknown`; `resolve_workflows` settles a
called workflow as `ordinary` when an ordinary workflow of the repository calls it
(`uses: ./.github/workflows/<file>`), else `unknown`. `gitlab_line_triggers(text)` gives
one trigger per line from its job: `when: manual` / `never` is `not_ordinary`; `only:`
and `rules:` are read for merge request and branch forms (`ordinary`), schedule, tag,
web, trigger and api forms (`not_ordinary`) and anything else (`unknown`); lines
outside a job are `ordinary`. `keep(lines, triggers, wanted)` blanks the other lines,
so `count_check_lines` counts each class. Known limits: `extends:`, `default:`,
anchors and `workflow: rules:` are not resolved; branch and path filters are not
read. Tests: `tests/test_ci_triggers.py`.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from lib.ci_masking import IndentTree, parse_lines, strip_comment
from lib.gitlab_rules import CONTEXTS, Context, Outcome, Rule, outcome

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


_Outcomes = dict[Context, Outcome]


def _only_outcomes(tree: IndentTree, key: int) -> _Outcomes:
    line = tree.lines[key]
    values: list[str]
    unknown: _Outcomes = {c: "unknown" for c in CONTEXTS}
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
            return unknown
    if not values:
        return unknown
    named = [v for v in values if v not in _SPECIAL_ONLY and v != "merge_requests"]
    push: Outcome = "yes" if {"branches", "pushes"} & set(named) else "some" if named else "no"
    return {"merge_request": "yes" if "merge_requests" in values else "no", "push": push}


def _rules(tree: IndentTree, key: int) -> list[Rule] | None:
    """The items of a ``rules:`` key, or None when it is not a block list read here."""
    if tree.lines[key].value:
        return None
    items = tree.items(key)
    if not items or not all(tree.lines[item[0]].dash for item in items):
        return None
    rules: list[Rule] = []
    for item in items:
        fields = {tree.lines[k].key: tree.lines[k].value for k in item}
        # A plain scalar continued on the next line (``if: $A == "push"`` then ``&& $B``)
        # parses as a keyless child: the rule is not read whole, so it is unread.
        if any(tree.lines[k].key in ("if", "when") and tree.children(k) for k in item):
            fields[""] = "continued"
        rules.append(Rule(fields))
    return rules


def _rules_outcomes(tree: IndentTree, key: int, default_when: str = "on_success") -> _Outcomes:
    rules = _rules(tree, key)
    if rules is None:
        return {c: "unknown" for c in CONTEXTS}
    return {c: outcome(rules, c, default_when) for c in CONTEXTS}


def _job_outcomes(tree: IndentTree, job: int, workflow_rules: bool) -> _Outcomes:
    keys = {tree.lines[k].key: k for k in tree.children(job)}
    when = tree.lines[keys["when"]].value.strip("'\"") if "when" in keys else ""
    if "rules" in keys:
        return _rules_outcomes(tree, keys["rules"], when or "on_success")
    if when in ("manual", "never", "on_failure"):
        return {c: "no" for c in CONTEXTS}
    if "only" in keys:
        return _only_outcomes(tree, keys["only"])
    # No rules: the default ``only: [branches, tags]`` leaves merge request pipelines out, unless
    # ``workflow: rules:`` creates them; then the job runs there too (merge_request_pipelines docs).
    return {"merge_request": "yes" if workflow_rules else "no", "push": "yes"}


def _workflow_outcomes(tree: IndentTree) -> _Outcomes | None:
    """Which ordinary pipelines a top-level ``workflow: rules:`` lets GitLab create."""
    for i, line in enumerate(tree.lines):
        if line.structural and line.level == 0 and line.key == "workflow":
            rules = [k for k in tree.children(i) if tree.lines[k].key == "rules"]
            if rules:
                return _rules_outcomes(tree, rules[0], "always")
    return None


def _trigger(job: _Outcomes, workflow: _Outcomes) -> Trigger:
    both: list[Outcome] = []
    for c in CONTEXTS:
        pair = (job[c], workflow[c])
        if "no" in pair:
            both.append("no")
        elif "unknown" in pair or pair == ("some", "some"):
            # Two branch limits may never meet (workflow on main, job on release): not read.
            both.append("unknown")
        else:
            both.append("yes")
    if "yes" in both:
        return "ordinary"
    return "unknown" if "unknown" in both else "not_ordinary"


def gitlab_line_triggers(text: str) -> list[Trigger]:
    """Per line of one GitLab CI file: does the job holding it run on ordinary pipelines?"""
    tree = IndentTree(parse_lines(text.splitlines()))
    workflow = _workflow_outcomes(tree)
    created: _Outcomes = workflow or {c: "yes" for c in CONTEXTS}
    # default: and a top-level before_script: run with the jobs, in the pipelines created.
    outside = _trigger({c: "yes" for c in CONTEXTS}, created)
    jobs: dict[int, Trigger] = {}
    out: list[Trigger] = []
    for i in range(len(tree.lines)):
        ancestors = tree.ancestors(i)
        top = ancestors[-1] if ancestors else None
        if top is None or tree.lines[top].level != 0 or tree.lines[top].key in _GITLAB_RESERVED:
            out.append(outside)
            continue
        if top not in jobs:
            jobs[top] = _trigger(_job_outcomes(tree, top, workflow is not None), created)
        out.append(jobs[top])
    return out


def keep(lines: Iterable[str], triggers: Iterable[Trigger], wanted: Trigger) -> list[str]:
    """``lines`` with every line whose trigger is not ``wanted`` blanked (indices kept)."""
    return [line if trigger == wanted else "" for line, trigger in zip(lines, triggers, strict=True)]
