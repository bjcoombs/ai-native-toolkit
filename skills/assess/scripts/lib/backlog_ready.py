"""Backlog kept agent-ready, level 1: the intake configuration committed at HEAD (#1322).

An agent handed an issue with no structure, never triaged, and judged against no written bar
starts from a guess. Level 1 of the "backlog kept agent-ready" practice (#1318) reads, from
committed files only, whether new issues are asked for structure, marked untriaged when they
arrive, and judged against a documented ready label, and whether a stale job can close an issue
that is ready. Rules only, no model, no network; the workflows and issue forms are read with
``lib.ci.ci_masking``'s indentation model and ``lib.ci.ci_triggers``, never a YAML parser. Every signal
is ``config_text``, so the level is listed, not counted.

The tendency it compensates for is unactioned intent at the tracker: the bar an issue must clear
lives in the maintainers' heads, so each issue an agent picks up restates it in review.

**Signals** (each a bool, or null with a sibling ``<signal>_reason`` code):

- ``required_form_field``: an issue form (``.github/ISSUE_TEMPLATE/*.yml`` or ``*.yaml``, not
  ``config.yml``) has a ``body`` item whose ``validations`` sets ``required: true``, or a GitLab
  issue template (``.gitlab/issue_templates/*.md``) has a heading for each of the six
  definition-of-ready sections (#1406, ``lib.backlog_ready_gitlab``). Null ``form_unreadable``
  when neither holds and a form or template could not be read (a template over the size cap) (a tab indent, an unclosed flow collection, no
  top-level ``body`` list).
- ``untriaged_on_open``: a workflow (directly under ``.github/workflows/``) triggered by
  ``issues`` with type ``opened`` (no ``types`` means every type) has a step that names an
  untriaged label as the argument of a label-adding call (``--add-label``, ``addLabels``, an ``add-labels``
  input, ``gh issue create --label``, or a ``labels:`` input of an action named for adding labels
  or ``labeler``); removing or listing by label is no sign, and ``name:``, ``if:`` and comment
  lines do not count. Null
  ``workflow_unreadable`` when none does and a workflow's ``on`` could not be read. When the
  repository has a ``.gitlab-ci.yml`` (GitLab has no CI trigger for a new issue), the
  toolkit's refinement template include or a scheduled job that adds an untriaged label also
  meets it (#1406, ``lib.backlog_ready_gitlab``): null ``gitlab_ci_unreadable`` for a root file
  GitLab rejects, null ``gitlab_ci_unsettled`` when an include, a merge or a rule this reader
  cannot settle may hold such a job.
- ``ready_label_documented``: a Markdown file at HEAD (``lib.docs.doc_graph.is_excluded_path``
  drops ``.assess/``, ``tests/fixtures/`` and the like) has a heading or paragraph naming a ready
  label with a list under it: a list item in the heading's section, or one after the paragraph.
  Fenced code is skipped. A label counts only in backticks or quotes, or as a GitLab label
  reference (``~agent-ready``), so prose ("the ready label",
  "make the code agent-ready") is no label; a one-word label counts only in backticks and keeps its
  case (a quoted "ready" or a `READY` constant is not ``ready``). A GitLab scoped label (``refine::ready``) is one token.
- ``stale_job_safe``: true when no step uses ``actions/stale``, or every such step leaves ready
  issues open: ``days-before-issue-stale`` / ``days-before-stale`` or ``days-before-issue-close``
  / ``days-before-close`` negative (never), or ``exempt-issue-labels`` naming a ready label (a
  block scalar's lines are joined), or a scope input (``only-labels``, ``only-issue-labels``,
  ``any-of-labels``, ``any-of-issue-labels``) that names no ready label. The action's default closes after 7 days, so a step with none of these is unsafe (false). Null
  ``stale_input_unresolved`` when no step is unsafe but one sets an input to an expression.

**Labels.** Matching ignores case, except a one-word label in documentation (above). ``UNTRIAGED_LABELS`` and ``READY_LABELS`` are the defaults; a
``[backlog_ready]`` table in ``.assess/config.toml`` replaces either list
(``assess_config.load_backlog_ready_labels``), and ``labels_from_config`` says so.

Block (``backlog_ready`` in run-context.json)::

    {"available": True, "required_form_field": bool | None, "untriaged_on_open": bool | None,
     "ready_label_documented": bool | None, "stale_job_safe": bool | None,
     "<signal>_reason": str (only beside a null signal),
     "forms": int, "forms_unreadable": int, "issue_templates": int, "workflows": int,
     "stale_jobs": int,
     "labels_from_config": bool}

Counts-only output keeps the booleans, the counts and the reason codes; no path, label or text
from a file leaves the run. The ``backlog-ready`` row is held in a test model
(``tests/fixtures/backlog_ready/levels.toml``) until #1324 places it, so its signals sit in
``signal_grades.STAGED_BLOCKS``. Tests: ``tests/test_backlog_ready.py``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal, NotRequired, TypedDict

from lib.assess_config import load_backlog_ready_labels
from lib.backlog_ready_gitlab import (GITLAB_CI, issue_templates, gitlab_untriaged, template_has_sections,
                                      unbalanced)
from lib.ci.ci_masking import IndentTree, parse_lines, strip_comment
from lib.ci.ci_triggers import workflow_events
from lib.instructions.command_index import ci_config_files, read_config, repo_files
from lib.docs.doc_graph import is_excluded_path

UNTRIAGED_LABELS = ("needs-triage", "needs triage", "untriaged", "triage", "status: needs triage")
READY_LABELS = ("agent-ready", "ready", "triage/accepted", "status: ready", "refine::ready")

REASON_FORM = "form_unreadable"
REASON_WORKFLOW = "workflow_unreadable"
REASON_STALE = "stale_input_unresolved"

FORM_DIR = ".github/ISSUE_TEMPLATE/"
_WORKFLOWS = ".github/workflows/"
_STALE = re.compile(r"""^["']?actions/stale(?:@|["']?$)""")
_ON = re.compile(r"""^(?:on|"on"|'on'|true)\s*:""")
# A call that adds a label: ``gh issue edit --add-label``, ``addLabels`` (github-script), an
# ``add-labels`` input, or ``gh issue create --label``. A ``labels:`` input counts only on a step
# whose action adds labels (``_ADDING_ACTION``); removing or listing by label is no sign.
_ADDS_LABEL = re.compile(r"--add-label\b|addLabels\b|\badd[-_]labels\b\s*:|\bgh\s+issue\s+create\b.*--label\b", re.I)
_ADDING_ACTION = re.compile(r"^\s*(?:-\s+)?uses\s*:\s*[\"']?[\w.-]+/[\w.-]*(?:add[-_]labels?|labeler)[\w.-]*", re.I)
_LABELS_INPUT = re.compile(r"^\s*(?:-\s+)?labels\s*:", re.I)
# The stale action's scope inputs: with one set, only issues carrying those labels go stale.
_STALE_SCOPE = ("only-labels", "only-issue-labels", "any-of-labels", "any-of-issue-labels")
_SKIP_KEYS = frozenset({"name", "if", "id"})
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_MD_SUFFIXES = (".md", ".markdown")

FormState = Literal["required", "none", "unreadable"]
StaleState = Literal["safe", "unsafe", "unresolved"]


class BacklogReadyBlock(TypedDict):
    """``run-context.json`` ``backlog_ready`` (see the module doc)."""

    available: bool
    required_form_field: bool | None
    required_form_field_reason: NotRequired[str]
    untriaged_on_open: bool | None
    untriaged_on_open_reason: NotRequired[str]
    ready_label_documented: bool | None
    stale_job_safe: bool | None
    stale_job_safe_reason: NotRequired[str]
    forms: int
    forms_unreadable: int
    issue_templates: int
    workflows: int
    stale_jobs: int
    labels_from_config: bool


def _token(label: str) -> re.Pattern[str]:
    """A label as a whole token: no word character, ``-`` or ``/`` on either side."""
    return re.compile(r"(?<![\w/-])" + re.escape(label) + r"(?![\w/-])", re.I)


def _named(label: str) -> re.Pattern[str]:
    """A label as written in documentation: in backticks or quotes, or as a GitLab label reference
    (``~agent-ready``, ``~"needs triage"``). A one-word label must sit in backticks or follow
    ``~`` and keep its case, so neither a quoted word ("ready") nor a constant (`READY`) is the
    label ``ready``; any other label ignores case."""
    word = re.escape(label)
    ref = rf"(?<![\w~])~{word}(?![\w:/-])" if not re.search(r"\s", label) else "(?!)"
    if re.search(r"[-/:\s]", label):
        return re.compile(rf"[`'\"]{word}[`'\"]|{ref}", re.I)
    return re.compile(rf"`{word}`|{ref}")


def _top(tree: IndentTree, key: str) -> int | None:
    return next((i for i, ln in enumerate(tree.lines)
                 if ln.structural and ln.indent == 0 and not ln.dash and ln.key == key), None)


def _child(tree: IndentTree, line: int, key: str) -> int | None:
    return next((k for k in tree.children(line) if tree.lines[k].key == key), None)


def _true(value: str) -> bool:
    return strip_comment(value).lower() == "true"


def _required(tree: IndentTree, validations: int) -> bool:
    value = tree.lines[validations].value
    if value.startswith("{"):
        return re.search(r"(?:^|[{,\s])required\s*:\s*true\s*(?:[,}]|$)", value, re.I) is not None
    req = _child(tree, validations, "required")
    return req is not None and _true(tree.lines[req].value)


def form_state(text: str) -> FormState:
    """``required`` when a ``body`` item's ``validations`` sets ``required: true``; ``unreadable``
    for a form GitHub would reject or this reader cannot follow; else ``none``."""
    lines = text.splitlines()
    if any(ln[:len(ln) - len(ln.lstrip(" \t"))].count("\t") for ln in lines):
        return "unreadable"
    tree = IndentTree(parse_lines(lines))
    if any(ln.structural and unbalanced(ln.value) for ln in tree.lines if ln.value[:1] in "[{"):
        return "unreadable"
    body = _top(tree, "body")
    if body is None or tree.lines[body].value or not tree.items(body):
        return "unreadable"
    for item in tree.items(body):
        for key in item:
            if tree.lines[key].key == "validations" and _required(tree, key):
                return "required"
    return "none"


def _forms(files: Iterable[str]) -> list[str]:
    return sorted(f for f in files if f.startswith(FORM_DIR) and "/" not in f[len(FORM_DIR):]
                  and f.endswith((".yml", ".yaml")) and PurePosixPath(f).stem.lower() != "config")


def _scalar(value: str) -> str:
    return strip_comment(value).strip().strip("'\"").strip()


def _issue_types(tree: IndentTree, lines: list[str]) -> list[str] | None:
    """The ``issues`` activity types the top-level ``on`` names; ``[]`` means every type."""
    on = next((i for i, raw in enumerate(lines) if _ON.match(raw)), None)
    if on is None:
        return []
    flow = strip_comment(lines[on].split(":", 1)[1])
    if flow.startswith("{"):  # ``on: {issues: {types: [labeled]}}``
        match = re.search(r"issues\s*:\s*\{[^{}]*?types\s*:\s*(\[[^\]]*\]|[\w-]+)", flow)
        return [] if match is None else [w.strip(" '\"") for w in match.group(1).strip("[]").split(",")]
    issues = _child(tree, on, "issues")
    if issues is None:
        return []  # a flow ``on: [issues]`` or ``on: issues``: every type
    value = tree.lines[issues].value
    if value.startswith("{"):
        match = re.search(r"types\s*:\s*(\[[^\]]*\]|[\w-]+)", value)
        return [] if match is None else [w.strip(" '\"") for w in match.group(1).strip("[]").split(",")]
    types = _child(tree, issues, "types")
    if types is None:
        return []
    inline = tree.lines[types].value
    if inline:
        return [w.strip(" '\"") for w in strip_comment(inline).strip("[]").split(",") if w.strip(" '\"")]
    return [_scalar(tree.lines[k].value) for k in tree.children(types)]


def _step_spans(tree: IndentTree, jobs: int) -> list[range]:
    """The line span of each step under ``jobs`` (an item of a job's ``steps:`` list)."""
    spans: list[range] = []
    for job in tree.children(jobs):
        steps = _child(tree, job, "steps")
        for item in tree.items(steps) if steps is not None else []:
            spans.append(range(min(item), max(tree.subtree(k).stop for k in item)))
    return spans


_GH_ADD_ARG = re.compile(r"""--add-label(?:\s+|=)("[^"]*"|'[^']*'|[^\s;&|]+)""")
_GH_CREATE_ARG = re.compile(r"""--label(?:\s+|=)("[^"]*"|'[^']*'|[^\s;&|]+)""")
_SEGMENTS = re.compile(r"\|\|?|;|&&")


def _gh_added(line: str) -> list[str]:
    """The labels a shell line's ``gh`` commands add: ``--add-label`` anywhere, ``--label`` only in a
    ``gh issue create`` segment (elsewhere it filters a list). Segments split on ``|``, ``;``, ``&&``."""
    out: list[str] = []
    for segment in _SEGMENTS.split(line):
        out += _GH_ADD_ARG.findall(segment)
        if re.search(r"\bgh\s+issue\s+create\b", segment):
            out += _GH_CREATE_ARG.findall(segment)
    return [arg.strip("'\"").replace(",", " , ") for arg in out]


def _call_window(kept: list[str], i: int) -> str:
    """Line ``i`` and the lines after it indented deeper: the call or input and its arguments."""
    indent = len(kept[i]) - len(kept[i].lstrip())
    out = [kept[i]]
    for line in kept[i + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return "\n".join(out)


def _added_text(kept: list[str]) -> str:
    """The text that names the labels one step adds: a ``gh`` flag's argument, or the window of an
    ``addLabels`` call, an ``add-labels`` input or an adding action's ``labels:`` input."""
    adding_action = any(_ADDING_ACTION.match(ln) for ln in kept)
    parts: list[str] = []
    for i, line in enumerate(kept):
        if re.search(r"\bgh\s", line) and _ADDS_LABEL.search(line):
            parts += _gh_added(line)
        elif _ADDS_LABEL.search(line) or (adding_action and _LABELS_INPUT.match(line)):
            parts.append(_call_window(kept, i))
    return "\n".join(parts)


def _applies_label(text: str, labels: tuple[str, ...]) -> bool:
    """True when one step adds an untriaged label (see the module doc)."""
    raw = text.splitlines()
    tree = IndentTree(parse_lines(raw))
    jobs = _top(tree, "jobs")
    if jobs is None:
        return False
    for span in _step_spans(tree, jobs):
        # each line keeps its indent, which bounds a call's argument window
        kept = [raw[i][:len(raw[i]) - len(raw[i].lstrip())] + strip_comment(raw[i]) for i in span
                if i < len(raw) and not raw[i].lstrip().startswith("#") and tree.lines[i].key not in _SKIP_KEYS]
        added = _added_text(kept)
        if added and any(_token(label).search(added) for label in labels):
            return True
    return False


def _untriaged(texts: dict[str, str], labels: tuple[str, ...]) -> tuple[bool | None, str | None]:
    unreadable = False
    for text in texts.values():
        events = workflow_events(text)
        if events is None:
            unreadable = True
            continue
        if "issues" not in events:
            continue
        lines = text.splitlines()
        types = _issue_types(IndentTree(parse_lines(lines)), lines)
        if types is None or (types and "opened" not in types):
            continue
        if _applies_label(text, labels):
            return True, None
    return (None, REASON_WORKFLOW) if unreadable else (False, None)


def _with_inputs(tree: IndentTree, uses: int) -> dict[str, str]:
    """A step's ``with:`` inputs; a block scalar (``>-``, ``|``) has its lines joined by commas."""
    step = tree.siblings[tree.mapping_of[uses]]
    with_key = next((k for k in step if tree.lines[k].key == "with"), None)
    if with_key is None:
        return {}
    inputs: dict[str, str] = {}
    for k in tree.children(with_key):
        value = tree.lines[k].value
        if re.match(r"^[|>][-+0-9]*$", value):
            owned = [tree.lines[i].value for i, owner in enumerate(tree.owner) if owner == k]
            value = ",".join(v for v in owned if v)
        inputs[tree.lines[k].key.lower()] = _scalar(value)
    return inputs


def _negative(value: str | None) -> bool | None:
    """True for a negative whole number, False for another number or none, None for an expression."""
    if value is None or value == "":
        return False
    if "${{" in value:
        return None
    try:
        return float(value) < 0
    except ValueError:
        return None


def stale_state(inputs: dict[str, str], ready: tuple[str, ...]) -> StaleState:
    """Whether one ``actions/stale`` step leaves ready issues open (see the module doc)."""
    wanted = {r.lower() for r in ready}
    scopes = [inputs[k] for k in _STALE_SCOPE if inputs.get(k)]
    if any("${{" in v for v in scopes):
        return "unresolved"
    if scopes and not any({n.strip().lower() for n in v.split(",")} & wanted for v in scopes):
        return "safe"  # scoped to labels that are not ready ones: a ready issue never goes stale
    exempt = inputs.get("exempt-issue-labels")
    if exempt is not None and "${{" not in exempt:
        names = {n.strip().lower() for n in exempt.split(",")}
        if names & {r.lower() for r in ready}:
            return "safe"
    unresolved = exempt is not None and "${{" in exempt
    for issue_key, any_key in (("days-before-issue-stale", "days-before-stale"),
                               ("days-before-issue-close", "days-before-close")):
        value = inputs.get(issue_key, inputs.get(any_key))
        never = _negative(value)
        if never:
            return "safe"
        unresolved = unresolved or never is None
    return "unresolved" if unresolved else "unsafe"


def _stale(texts: dict[str, str], ready: tuple[str, ...]) -> tuple[bool | None, str | None, int]:
    states: list[StaleState] = []
    for text in texts.values():
        tree = IndentTree(parse_lines(text.splitlines()))
        for i, line in enumerate(tree.lines):
            if line.structural and line.key == "uses" and _STALE.match(line.value) and i in tree.mapping_of:
                states.append(stale_state(_with_inputs(tree, i), ready))
    if "unsafe" in states:
        return False, None, len(states)
    if "unresolved" in states:
        return None, REASON_STALE, len(states)
    return True, None, len(states)


def documents_ready_label(text: str, ready: tuple[str, ...]) -> bool:
    """True when a heading or paragraph naming a ready label has a list under it."""
    patterns = [_named(label) for label in ready]
    armed_heading: int | None = None  # the level of a heading that names a label
    in_para = para_names = pending = False
    fence = ""  # the marker that opened the current fenced block
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker is not None and (not fence or marker.group(1) == fence):
            fence = "" if fence else marker.group(1)
            continue
        if fence:
            continue
        names = any(p.search(line) for p in patterns)
        heading = _HEADING.match(line)
        if heading is not None:
            level = len(heading.group(1))
            if names:
                armed_heading = level
            elif armed_heading is not None and level <= armed_heading:
                armed_heading = None
            in_para = para_names = pending = False
        elif not line.strip():
            pending, in_para, para_names = in_para and para_names, False, False
        elif _LIST_ITEM.match(line):
            if armed_heading is not None or para_names or pending:
                return True
            in_para = para_names = pending = False
        else:
            para_names = names or (in_para and para_names)
            in_para, pending = True, False
    return False


def _docs(root: Path, files: Iterable[str], ready: tuple[str, ...]) -> bool:
    for rel in sorted(files):
        if rel.lower().endswith(_MD_SUFFIXES) and not is_excluded_path(Path(rel)):
            if documents_ready_label(read_config(root, rel), ready):
                return True
    return False


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def scan_backlog_ready(repo_root: Path) -> BacklogReadyBlock:
    """Build the ``backlog_ready`` block (see the module doc). Intake is configured for the whole
    repository, so the root's files are read whatever the scope."""
    root = repo_root.resolve()
    files = repo_files(root)
    untriaged, ready, from_config = load_backlog_ready_labels(root, UNTRIAGED_LABELS, READY_LABELS)
    forms = _forms(files)
    states = [form_state(read_config(root, rel)) for rel in forms]
    workflows = {rel: read_config(root, rel) for rel in ci_config_files(files) if rel.startswith(_WORKFLOWS)}
    templates = issue_templates(files)
    block: BacklogReadyBlock = {
        "available": True, "required_form_field": None, "untriaged_on_open": None,
        "ready_label_documented": _docs(root, files, ready), "stale_job_safe": None,
        "forms": len(forms), "forms_unreadable": states.count("unreadable"),
        "issue_templates": len(templates), "workflows": len(workflows), "stale_jobs": 0, "labels_from_config": from_config,
    }
    texts = {rel: read_config(root, rel) for rel in templates}
    # an empty read of a non-empty file is a template over the size cap or not readable
    unread = [rel for rel, text in texts.items() if not text and _size(root / rel) > 0]
    if "required" in states or any(template_has_sections(text) for text in texts.values()):
        block["required_form_field"] = True
    elif "unreadable" in states or unread:
        block["required_form_field_reason"] = REASON_FORM
    else:
        block["required_form_field"] = False
    block["untriaged_on_open"], reason = _untriaged(workflows, untriaged)
    if block["untriaged_on_open"] is not True and GITLAB_CI in files:
        gitlab, gitlab_reason = gitlab_untriaged(root, read_config(root, GITLAB_CI), untriaged)
        if gitlab is True or (gitlab is None and block["untriaged_on_open"] is False):
            block["untriaged_on_open"], reason = gitlab, gitlab_reason
    if reason is not None:
        block["untriaged_on_open_reason"] = reason
    block["stale_job_safe"], reason, block["stale_jobs"] = _stale(workflows, ready)
    if reason is not None:
        block["stale_job_safe_reason"] = reason
    return block
