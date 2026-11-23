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
- ``except:`` with the same reader, taken away from what ``only:`` (or its default) runs:
  ``except: [branches]`` runs in neither, ``except: [main]`` on some branches, and an
  ``except:`` map with a key other than ``refs`` (``variables:``) is ``unknown`` unless
  ``only:`` already runs in neither (#1021).
- ``extends:`` (#1021): a name or a list of names, resolved through the top-level keys of
  the same file, multi-level, in GitLab's merge order: parents in order with the last
  winning, then the job's own keys. ``rules:`` and ``when:`` overwrite, so a job's own
  ``rules:`` replace a template's whole; two ``only:`` or ``except:`` maps would be
  deep-merged and read ``unknown``. A target not in the file (one from ``include:``), a
  cycle, an anchor or tag value, and a merged job holding ``rules:`` beside ``only:`` or
  ``except:`` (GitLab rejects it) read ``unknown``, never as a job without rules.
- No such key: branch pushes, and merge requests too when ``workflow: rules:`` exists.
- A top-level ``workflow: rules:`` decides which of the two pipelines GitLab creates,
  with the same reader; a job runs only in a pipeline that is created.
- Lines outside a job (``default:``, a top-level ``before_script:``) are ``ordinary``
  when ``workflow: rules:`` (if any) creates either pipeline.
- A job limited to some branches inside a workflow limited to some branches is
  ``unknown``: the two limits are not compared.

Known limits: ``extends:`` across ``include:`` reads ``unknown``; rules and ``when``
inherited through ``default:`` or a YAML anchor are not read; ``workflow: rules:`` is read only
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
so `count_check_lines` counts each class. Same-file `extends:` is resolved before
`rules:`, `when:`, `only:` and `except:` are read (#1021). Known limits: `default:`,
anchors, `extends:` across `include:` and `workflow: rules:` across files are not
resolved; branch and path filters are not read. Tests: `tests/test_ci_triggers.py`, and
`tests/test_gitlab_extends.py` for `extends:` and `except:`.
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


def _only_outcomes(tree: IndentTree, key: int, refs_only: bool = False) -> _Outcomes:
    """Which ordinary pipelines an ``only:`` (or ``except:``) names; ``refs_only`` makes a map
    with any key besides ``refs`` unknown, since an unread ``except: variables:`` may stop a run."""
    line = tree.lines[key]
    values: list[str]
    unknown: _Outcomes = {c: "unknown" for c in CONTEXTS}
    if line.value:
        values = _names(line.value)
    else:
        children = tree.children(key)
        refs = [k for k in children if tree.lines[k].key == "refs"]
        if refs_only and refs and len(refs) != len(children):
            return unknown
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


# The job keys this reader takes through ``extends:``.
_READ_KEYS = ("rules", "when", "only", "except")
# Each read key's defining lines, nearest the job last; None when ``extends:`` is unresolved.
_Keys = dict[str, list[int]]


def _parents(tree: IndentTree, key: int) -> list[str] | None:
    """The job names an ``extends:`` value lists, or None when it is not a plain name or list."""
    line = tree.lines[key]
    if line.value:
        names = _names(line.value) if line.value.startswith("[") else [line.value.strip("'\"")]
    else:
        children = tree.children(key)
        if not children or not all(tree.lines[k].dash and not tree.lines[k].key for k in children):
            return None
        names = [tree.lines[k].value.strip("'\"") for k in children]
    if not names or any(not n or n[0] in "&*!{|>" for n in names):
        return None
    return names


def _job_keys(tree: IndentTree, job: int, top: dict[str, int], seen: frozenset[int],
              cache: dict[int, _Keys | None]) -> _Keys | None:
    """The read keys of ``job`` after ``extends:``: parents in order, the last winning, then the
    job's own keys. None when a parent is not a top-level key of this file, or on a cycle."""
    if job in cache:
        return cache[job]
    own = {tree.lines[k].key: k for k in tree.children(job)}
    merged: _Keys = {}
    if "extends" in own:
        names = _parents(tree, own["extends"])
        if names is None:
            cache[job] = None
            return None
        for name in names:
            parent = top.get(name)
            inherited = None if parent is None or parent in seen else _job_keys(
                tree, parent, top, seen | {job}, cache)
            if inherited is None:
                cache[job] = None
                return None
            for read, lines in inherited.items():
                merged[read] = merged.get(read, []) + lines
    for read in _READ_KEYS:
        if read in own:
            merged[read] = merged.get(read, []) + [own[read]]
    cache[job] = merged
    return merged


def _is_map(tree: IndentTree, key: int) -> bool:
    children = tree.children(key)
    return not tree.lines[key].value and bool(children) and not all(tree.lines[k].dash for k in children)


def _merged(tree: IndentTree, keys: _Keys, read: str) -> int | None:
    """The line that decides ``read``, or -1 when GitLab deep-merges two maps (not read here)."""
    lines = keys.get(read)
    if not lines:
        return None
    # extends: deep-merges hashes and overwrites anything else: only: {refs} over only: {variables}.
    if read in ("only", "except") and _is_map(tree, lines[-1]) and any(_is_map(tree, k) for k in lines[:-1]):
        return -1
    return lines[-1]


def _except(base: _Outcomes, excluded: _Outcomes) -> _Outcomes:
    """``only:`` outcomes less the pipelines ``except:`` names."""
    out: _Outcomes = {}
    for c in CONTEXTS:
        runs, stops = base[c], excluded[c]
        if runs == "no" or stops == "no":
            out[c] = runs
        elif stops == "yes":
            out[c] = "no"
        else:
            # except: [main] under the default only: some branches; two branch limits are not compared.
            out[c] = "some" if (runs, stops) == ("yes", "some") else "unknown"
    return out


def _job_outcomes(tree: IndentTree, keys: _Keys | None, workflow_rules: bool) -> _Outcomes:
    unknown: _Outcomes = {c: "unknown" for c in CONTEXTS}
    if keys is None:
        return unknown
    rules, when_key, only, excepted = (_merged(tree, keys, read) for read in _READ_KEYS)
    if -1 in (only, excepted):
        return unknown
    when = tree.lines[when_key].value.strip("'\"") if when_key is not None else ""
    if rules is not None:
        # GitLab rejects a job with rules: beside only: or except:, as extends: can make one.
        if only is not None or excepted is not None:
            return unknown
        return _rules_outcomes(tree, rules, when or "on_success")
    if when in ("manual", "never", "on_failure"):
        return {c: "no" for c in CONTEXTS}
    # No only: means the default ``only: [branches, tags]``, which leaves merge request pipelines out
    # unless ``workflow: rules:`` creates them; then the job runs there too (merge_request_pipelines docs).
    default: _Outcomes = {"merge_request": "yes" if workflow_rules else "no", "push": "yes"}
    base = _only_outcomes(tree, only) if only is not None else default
    if excepted is not None:
        base = _except(base, _only_outcomes(tree, excepted, refs_only=True))
    return base


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
    top = {line.key: i for i, line in enumerate(tree.lines)
           if line.structural and line.level == 0 and not line.dash and line.key}
    cache: dict[int, _Keys | None] = {}
    out: list[Trigger] = []
    for i in range(len(tree.lines)):
        ancestors = tree.ancestors(i)
        job = ancestors[-1] if ancestors else None
        if job is None or tree.lines[job].level != 0 or tree.lines[job].key in _GITLAB_RESERVED:
            out.append(outside)
            continue
        if job not in jobs:
            keys = _job_keys(tree, job, top, frozenset(), cache)
            jobs[job] = _trigger(_job_outcomes(tree, keys, workflow is not None), created)
        out.append(jobs[job])
    return out


def keep(lines: Iterable[str], triggers: Iterable[Trigger], wanted: Trigger) -> list[str]:
    """``lines`` with every line whose trigger is not ``wanted`` blanked (indices kept)."""
    return [line if trigger == wanted else "" for line, trigger in zip(lines, triggers, strict=True)]
