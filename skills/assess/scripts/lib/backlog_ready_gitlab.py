"""Backlog kept agent-ready, level 1 on GitLab: issue templates and the scheduled refiner (#1406).

``lib.backlog_ready`` reads GitHub issue forms and workflows; this module reads their GitLab
counterparts from committed files, with the same rule that a piece it cannot read is not
measured, never unmet. Rules only, no model, no network, no YAML parser.

**Issue templates** (``.gitlab/issue_templates/*.md``, directly in that folder). GitLab has no
required field, so a template stands in for a form with one when it holds a heading for each of
the six definition-of-ready sections (``READY_SECTIONS``: Problem Statement, Technical Context,
Impact, Root Cause, Solution Requirements, Success Criteria), at any heading level, compared
ignoring case, emphasis and a trailing colon. Headings in fenced code do not count.

**The refiner** (``.gitlab-ci.yml``). GitLab has no CI trigger for a new issue, so the counterpart
of a workflow on ``issues: opened`` is a scheduled job that labels new issues. ``untriaged`` is
met by either:

- the toolkit's refinement template as a top-level ``include:`` item, its exact ``remote:``
  raw URL (``templates/refine.yml`` of this repository at any ref); an include with ``rules:``
  may be left out, so it is unsettled, never met, and so is one under a ``workflow: rules:``
  that creates only some scheduled pipelines or cannot be read;
- a visible job that runs in a scheduled pipeline and whose ``script``, ``before_script`` or
  ``after_script`` adds an untriaged label: ``glab issue update|create --label`` / ``-l``, an
  ``add_labels`` API field, or a ``/label ~name`` quick action in a ``glab`` or ``curl`` call. A label is compared whole,
  ignoring case; a ``$VARIABLE`` argument is unsettled.

Each job is read as GitLab merges it (``ci_includes.merged_config``, rendered by
``ci_merged_view.rendered_job``): ``include: local:`` files, ``extends:`` and ``default:``
applied. Hidden jobs (``.name``) never run on their own and are not read as jobs.
A ``workflow: rules:`` guard and a job guard that each hold only in some scheduled pipelines
(two schedule variables) are unsettled, not met. A job runs in
a scheduled pipeline when its ``rules:`` name ``schedule`` and ``gitlab_rules.outcome`` in the
``schedule`` context reads yes or some (``when: never`` / ``manual`` rules are no run), or its
``only:`` names ``schedules`` and its ``except:`` does not, with no job-level ``when: never`` or
``manual``. A job with neither is not a scheduled job, though it runs in scheduled pipelines,
except that ``rules:`` gated on something else (``$REFINE == "true"``, set by a schedule) that
may run on a schedule and does not plainly run on every push or merge request is unsettled.
A top-level ``workflow: rules:`` that creates no scheduled pipeline stops every job.

**Unsettled** (``REASON_UNSETTLED``): when nothing meets, the signal is null rather than false
if an include other than a GitLab ``template:`` was not read offline (it may define the job), if
the refinement include sits under ``rules:``, or if a job that adds a label or may add one cannot
be settled: a merge the reader does not follow (an anchor merge, an unread ``extends:`` parent),
a ``!reference``, a ``$VARIABLE`` label or a ``rules:`` outcome that reads unknown. An anchor
merge is file-local, so a job with one is unsettled only when its file adds an untriaged label
somewhere. **Unreadable** (``REASON_UNREADABLE``): the root ``.gitlab-ci.yml`` has a tab in a key
line's indentation or a flow collection never closed, which GitLab rejects, so nothing it includes runs.

Known limits: a vendored copy of the refinement template (a ``local:`` include) is read like any
other file, and its jobs take the label from a variable, so it reads unsettled. A project CI
file at a custom path, a pipeline schedule's existence and its variables are project settings
this reader cannot see. Tests: ``tests/test_backlog_ready.py``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from lib.ci.ci_includes import (SETTLED, MergedConfig, MergedJob, include_items, merged_config,
                                resolve_includes)
from lib.ci.ci_masking import IndentTree, parse_lines, strip_comment
from lib.ci.ci_merged_view import rendered_job
from lib.ci.ci_triggers import rule_items
from lib.gitlab_rules import outcome

GITLAB_CI = ".gitlab-ci.yml"
TEMPLATE_DIR = ".gitlab/issue_templates/"
READY_SECTIONS = ("Problem Statement", "Technical Context", "Impact", "Root Cause",
                  "Solution Requirements", "Success Criteria")
REASON_UNREADABLE = "gitlab_ci_unreadable"
REASON_UNSETTLED = "gitlab_ci_unsettled"

# The toolkit's refinement template, matched exactly (templates/refine.yml of this repository).
REFINE_REMOTE = re.compile(
    r"^https://raw\.githubusercontent\.com/bjcoombs/ai-native-toolkit/[^/\s]+/templates/refine\.yml$")

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_SCRIPT_KEYS = ("before_script", "script", "after_script")
_SEGMENTS = re.compile(r"\|\|?|;|&&")
_GLAB_ISSUE = re.compile(r"\bglab\s+issue\s+(?:update|create|new)\b")
_GLAB_LABEL = re.compile(r"""(?<![\w-])(?:--label|-l)(?:\s+|=)("[^"]*"|'[^']*'|[^\s;&|]+)""")
_ADD_LABELS = re.compile(r"""\badd_labels["']?\s*[=:]\s*("[^"]*"|'[^']*'|[^\s;&|"']+)""")
# A quick action counts only where it is posted: a glab or curl call, or a notes API path.
_POSTS = re.compile(r"\bglab\b|\bcurl\b|/notes\b")
_QUICK_LABEL = re.compile(r"""(?:^|[\s"'])/label((?:\s+~(?:"[^"]*"|[^\s"'~]+))+)""")
_QUICK_ARG = re.compile(r"""~("[^"]*"|[^\s"'~]+)""")
_BLOCK_SCALAR = re.compile(r"^[|>][-+0-9]*$")
_RUN_WHEN = frozenset({"on_success", "always", "delayed"})
_STOP_WHEN = frozenset({"never", "manual", "on_failure"})

JobState = Literal["met", "unsettled", "no"]
# ``some``: only in some scheduled pipelines (a branch, a schedule's own variable).
Schedule = Literal["yes", "some", "no", "unknown"]


def unbalanced(value: str) -> bool:
    """True when a flow value opens more brackets than it closes, outside quotes."""
    depth = 0
    quote = ""
    for ch in value:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth != 0 or bool(quote)


# --- issue templates --------------------------------------------------------------------

def issue_templates(files: frozenset[str]) -> list[str]:
    """The Markdown issue templates directly under ``.gitlab/issue_templates/``."""
    return sorted(f for f in files if f.startswith(TEMPLATE_DIR) and "/" not in f[len(TEMPLATE_DIR):]
                  and f.lower().endswith(".md"))


def template_has_sections(text: str) -> bool:
    """True when a heading names each of ``READY_SECTIONS`` (see the module doc)."""
    found: set[str] = set()
    fence = ""
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker is not None and not fence:
            fence = marker.group(1)
            continue
        if marker is not None and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) \
                and not line.strip()[len(marker.group(1)):].strip():
            fence = ""  # a closing fence: the same character, at least as long, nothing after it
            continue
        heading = None if fence else _HEADING.match(line)
        if heading is not None:
            found.add(heading.group(1).strip("*_ ").rstrip(":").strip("*_ ").lower())
    return all(s.lower() in found for s in READY_SECTIONS)


# --- the refiner ------------------------------------------------------------------------

def malformed(text: str) -> bool:
    """A tab in a key line's indentation, or a flow collection never closed: GitLab rejects
    either. A block scalar's lines (a heredoc's tabs) are not keys, and a flow collection may run
    over several lines (``only: [main,`` then ``schedules]``)."""
    lines = text.splitlines()
    parsed = parse_lines(lines)
    scalar = -1  # the indent of the line opening a block scalar; deeper lines are its text
    for i, ln in enumerate(parsed):
        raw = lines[i]
        spaces = len(raw) - len(raw.lstrip(" "))
        if scalar >= 0 and (not raw.strip() or spaces > scalar):
            continue
        scalar = ln.indent if ln.structural and _BLOCK_SCALAR.match(ln.value) else -1
        if not ln.structural:
            continue
        if "\t" in raw[:len(raw) - len(raw.lstrip(" \t"))]:
            return True
        if ln.value[:1] in "[{" and unbalanced(ln.value):
            joined = ln.value
            for nxt in lines[i + 1:]:
                joined += " " + strip_comment(nxt)
                if not unbalanced(joined):
                    break
            else:
                return True
    return False


def _labels_added(line: str) -> list[str]:
    """The labels one script line adds (see the module doc), each as written, unquoted."""
    out: list[str] = []
    for segment in _SEGMENTS.split(line):
        if _GLAB_ISSUE.search(segment):
            out += _GLAB_LABEL.findall(segment)
        out += _ADD_LABELS.findall(segment)
        for quick in _QUICK_LABEL.findall(segment) if _POSTS.search(segment) else ():
            out += _QUICK_ARG.findall(quick)
    return [name.strip(" '\"") for arg in out for name in arg.strip("'\"").split(",") if name.strip(" '\"")]


def _adds(lines: list[str], labels: tuple[str, ...]) -> JobState:
    """``met`` when the lines add an untriaged label, ``unsettled`` when one adds a label held in a
    variable, else ``no``. A line ending in ``\\`` continues on the next."""
    wanted = {label.lower() for label in labels}
    joined: list[str] = []
    carry = ""
    for raw in lines:
        if raw.lstrip().startswith("#"):
            continue
        text = carry + strip_comment(raw)
        if text.endswith("\\"):
            carry = text[:-1] + " "
            continue
        joined.append(text)
        carry = ""
    joined.append(carry)
    added = [name for line in joined for name in _labels_added(line)]
    if any(name.lower() in wanted for name in added):
        return "met"
    return "unsettled" if any("$" in name for name in added) else "no"


def _refs(tree: IndentTree, key: int) -> list[str] | None:
    """The ref names of an ``only:`` / ``except:``, or None for a form this reader does not read
    (a flow map, a map with a key besides ``refs``)."""
    value = tree.lines[key].value
    if value.startswith("{") or unbalanced(value):
        return None  # a flow map, or a flow list continued on the next line
    if value:
        return [v.strip(" '\"") for v in value.strip("[]").split(",") if v.strip(" '\"")]
    children = tree.children(key)
    if children and all(tree.lines[k].dash and not tree.lines[k].key for k in children):
        return [tree.lines[k].value.strip(" '\"") for k in children]
    if [tree.lines[k].key for k in children] == ["refs"]:
        return _refs(tree, children[0])
    return None


def _plain(value: str) -> str:
    return strip_comment(value).strip(" '\"")


def _schedule(tree: IndentTree, keys: dict[str, int], text: list[str]) -> Schedule:
    """Whether the rendered job is written to run in a scheduled pipeline (see the module doc)."""
    when = _plain(tree.lines[keys["when"]].value) if "when" in keys else "on_success"
    if "rules" in keys:
        if "only" in keys or "except" in keys:
            return "unknown"  # GitLab rejects rules: beside only: / except:
        span = "\n".join(text[i] for i in tree.subtree(keys["rules"]))
        rules = None if "!reference" in span else rule_items(tree, keys["rules"])
        if rules is None:
            return "unknown"
        result = outcome(rules, "schedule", when)
        if "schedule" not in span:
            # gated on something else (a schedule's own $REFINE): unsettled while it may run on a
            # schedule and does not plainly run on every push or merge request
            ordinary = {outcome(rules, c, when) for c in ("push", "merge_request")}
            return "unknown" if result != "no" and "yes" not in ordinary else "no"
        return result
    if when in _STOP_WHEN:
        return "no"
    if when not in _RUN_WHEN:
        return "unknown"
    if "only" not in keys:
        return "no"
    only = _refs(tree, keys["only"])
    if only is None:
        return "unknown"
    if "schedules" not in only:
        return "no"
    if "except" not in keys:
        return "yes"
    excepted = _refs(tree, keys["except"])
    return "unknown" if excepted is None else "no" if "schedules" in excepted else "yes"


def _workflow(config: MergedConfig) -> Schedule:
    """Whether ``workflow: rules:`` lets GitLab create a scheduled pipeline."""
    flow = config.workflow
    if flow is None or "rules" not in flow.keys:
        return "yes"
    text, settled = rendered_job(config, flow)
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    key = next((k for k in tree.children(0) if tree.lines[k].key == "rules"), None)
    span = "" if key is None else "\n".join(lines[i] for i in tree.subtree(key))
    rules = None if key is None or "!reference" in span else rule_items(tree, key)
    if flow.unresolved is not None or not settled or rules is None:
        return "unknown"
    return outcome(rules, "schedule", "always")


def _job_state(config: MergedConfig, job: MergedJob, labels: tuple[str, ...],
               workflow: Schedule) -> JobState:
    """``met``, ``unsettled`` or ``no`` for one visible job (see the module doc). Met needs a
    scheduled pipeline that both the workflow and the job are sure of: two guards that each
    hold only in some scheduled pipelines may never hold together."""
    text, settled = rendered_job(config, job)
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    keys = {tree.lines[k].key: k for k in tree.children(0)}
    script = [lines[i] for key in _SCRIPT_KEYS if key in keys for i in tree.subtree(keys[key])]
    adds = "unsettled" if any("!reference" in line for line in script) else _adds(script, labels)
    if adds == "no" and job.unresolved == "unreadable":
        # an anchor merge or alias brings lines from the same file: settled only if no line there adds the label
        sources = [config.sources[d.source].splitlines() for d in job.defs]
        adds = "unsettled" if any(_adds(src, labels) != "no" for src in sources) else "no"
    if adds == "no":
        return "no"
    schedule = _schedule(tree, keys, lines)
    if schedule == "no":
        # an anchor merge may bring the schedule selector this job does not show
        hidden_selector = job.unresolved == "unreadable" and not {"rules", "only", "except", "when"} & set(keys)
        return "unsettled" if hidden_selector else "no"
    sure = "yes" in (schedule, workflow) and {schedule, workflow} <= {"yes", "some"}
    if adds == "met" and sure and settled and job.unresolved is None:
        return "met"
    return "unsettled"


def _refine_include(text: str) -> JobState:
    items, _section, _dropped = include_items(text)
    states = [("unsettled" if item.conditional else "met") for item in items
              if item.kind == "remote" and REFINE_REMOTE.match(item.source.strip().strip("\"'"))]
    return "met" if "met" in states else "unsettled" if states else "no"


def gitlab_untriaged(root: Path, text: str, labels: tuple[str, ...]) -> tuple[bool | None, str | None]:
    """Whether ``.gitlab-ci.yml`` (``text``) runs a refiner that labels new issues untriaged:
    the signal and its reason code (see the module doc). ``root`` is resolved."""
    if malformed(text):
        return None, REASON_UNREADABLE
    config = merged_config(root)
    if config is None:
        return None, REASON_UNREADABLE
    workflow = _workflow(config)
    if workflow == "no":
        return False, None  # GitLab creates no scheduled pipeline, so no job or include runs on one
    include = _refine_include(text)
    if include == "met" and workflow == "yes":
        return True, None  # the included jobs run in every scheduled pipeline the workflow creates
    unsettled = include != "no"
    unread = [r for r in resolve_includes(root).includes
              if r["status"] not in SETTLED and r["kind"] != "template"]
    unsettled = unsettled or bool(unread)
    for job in config.jobs.values():
        if job.hidden:
            continue
        state = _job_state(config, job, labels, workflow)
        if state == "met":
            return True, None
        unsettled = unsettled or state != "no"
    return (None, REASON_UNSETTLED) if unsettled else (False, None)
