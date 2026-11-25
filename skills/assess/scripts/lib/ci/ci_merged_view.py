"""GitLab jobs as GitLab merges them, for the line readers (issue #1180).

``ci_checks`` reads each GitLab CI file line by line, and each included file on its own. A job
whose keys come from more than one place reads wrong that way: a root ``lint: when: manual``
over an included ``lint:`` job, a root ``script:`` that replaces the included one, an
``allow_failure: true`` from an ``extends:`` template, a ``default: before_script:`` that every
job replaces. ``merged_view(config)`` takes ``ci_includes.merged_config`` and returns a
``MergedView``:

- ``texts``: each source (the root file, then ``Resolution.files``) with the lines of every
  merged job blanked, line count kept. A merged job is a visible job defined in more than one
  file, one with ``extends:``, or one that takes ``before_script:`` / ``after_script:`` from
  ``default:``. The hidden templates such a job extends and the ``default:`` script keys are
  blanked too; they run only inside the jobs that take them.
- ``rendered``: one GitLab CI text holding the effective ``workflow:`` and each merged job,
  renamed ``job<n>``, with its effective keys: the last definition of each key, consecutive
  maps deep-merged (``ci_includes`` holds the precedence). The line readers read it like any
  other file, so masking (``allow_failure``) and triggers (``rules:``, ``when:``) apply.
- ``unsettled``: the rendered lines whose job or workflow is ``unresolved``, or whose maps
  could not be merged (a flow map beside a block map). ``ci_checks`` reads their triggers as
  unknown, so they read as not measured, never as met.

A job that is not merged keeps its own lines and its own reading. So does a job merged only
by a ``default:`` script key whose own definition is ``unreadable`` (an anchor merge): it reads as
before #1180, and the ``default:`` lines stay in place for it. ``MergedJob.open`` (an include
this run could not read may also define the job) is not applied here: ``ci_checks`` keeps
#766's reading of such a job, and its ``included_jobs`` basis covers a missing check. Inward-only
imports: ``lib.ci.ci_includes``. Tests: ``tests/test_ci_merged_config.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

from lib.ci.ci_includes import KeyDef, MergedConfig, MergedJob

SCRIPT_KEYS = frozenset({"script", "before_script", "after_script"})


@dataclass(frozen=True)
class MergedView:
    texts: tuple[str, ...]
    rendered: str
    unsettled: frozenset[int]


@dataclass(frozen=True)
class _Node:
    """One key and its value: ``children`` for a block map this reader parsed, else None."""

    head: str  # the key as written
    lines: tuple[str, ...]  # its raw lines, key line first
    col: int
    children: dict[str, _Node] | None
    map_like: bool  # a map, parsed or not (a flow map, one with an unread line)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _node(lines: list[str], start: int, end: int, col: int) -> _Node:
    head, _, value = lines[start].strip().partition(":")
    value = value.split(" #", 1)[0].strip()
    raw = tuple(lines[start:end])
    body = [(j, lines[j]) for j in range(start + 1, end)
            if lines[j].strip() and not lines[j].strip().startswith("#")]
    if value.startswith("{"):
        return _Node(head, raw, col, None, True)
    if (value and not value.startswith("&")) or not body or body[0][1].strip().startswith("-"):
        return _Node(head, raw, col, None, False)
    child_col = _indent(body[0][1])
    # A sequence at its key's own indent (``paths:`` then ``- x``) belongs to that key.
    keys = [j for j, line in body if _indent(line) == child_col and not line.strip().startswith("-")]
    children: dict[str, _Node] = {}
    for n, j in enumerate(keys):
        name, colon, _rest = lines[j].strip().partition(":")
        if not colon or name.startswith("-") or name in children or not name.strip():
            return _Node(head, raw, col, None, True)
        children[name.strip()] = _node(lines, j, keys[n + 1] if n + 1 < len(keys) else end, child_col)
    return _Node(head, raw, col, children, True)


def _merge(nodes: list[_Node]) -> _Node | None:
    """GitLab's deep merge: a map over a map merges key by key, anything else replaces."""
    current: _Node | None = None
    for node in nodes:
        if current is None or not (current.map_like and node.map_like):
            current = node
            continue
        if current.children is None or node.children is None:
            return None   # a flow map or an unread line beside another map: not merged here
        merged = dict(current.children)
        for name, child in node.children.items():
            both = _merge([merged[name], child]) if name in merged else child
            if both is None:
                return None
            merged[name] = both
        current = _Node(node.head, (), node.col, merged, True)
    return current


def _emit(node: _Node, indent: int) -> list[str]:
    if node.children is not None and (not node.lines or node.children):
        out = [" " * indent + node.head + ":"]
        for child in node.children.values():
            out.extend(_emit(child, indent + 2))
        return out
    shift = indent - node.col
    return [(" " * shift + line if shift >= 0 else line[min(-shift, _indent(line)):])
            if line.strip() else "" for line in node.lines]


def _render(config: MergedConfig, keys: dict[str, tuple[KeyDef, ...]],
            lines: list[list[str]]) -> tuple[list[str], bool]:
    """A job's effective keys at indent 2, and whether every merge was read."""
    out: list[str] = []
    settled = True
    for chain in keys.values():
        nodes = [_node(lines[k.source], k.start, k.end, k.col) for k in chain]
        node = _merge(nodes)
        if node is None:
            settled, node = False, nodes[-1]
        out.extend(_emit(node, 2))
    return out, settled


def _merged(job: MergedJob) -> bool:
    if job.hidden:
        return False
    if len(job.defs) > 1 or job.extended:
        return True
    # A job whose only other source is a default: script key keeps its own reading when its own
    # definition is not read whole (an anchor merge): merging it would make it unknown.
    return bool(job.inherited & SCRIPT_KEYS) and job.unresolved != "unreadable"


def merged_jobs(config: MergedConfig) -> list[MergedJob]:
    """The visible jobs whose keys GitLab merges from more than one place."""
    return [job for job in config.jobs.values() if _merged(job)]


def merged_view(config: MergedConfig) -> MergedView:
    """The sources with merged jobs blanked, and those jobs rendered (module docstring)."""
    lines = [text.splitlines() for text in config.sources]
    blank: dict[int, set[int]] = {}

    def drop(source: int, start: int, end: int) -> None:
        blank.setdefault(source, set()).update(range(start, end))

    jobs = merged_jobs(config)
    for job in jobs:
        for d in job.defs:
            drop(d.source, d.start, d.end)
        for parent in job.extended:
            if parent.startswith(".") and parent in config.jobs:
                for d in config.jobs[parent].defs:
                    drop(d.source, d.start, d.end)
    # A job left on its own reading still runs the default: script lines as written there.
    kept = any(not job.hidden and job.inherited & SCRIPT_KEYS and not _merged(job)
               for job in config.jobs.values())
    if config.default is not None and not kept:
        for key in SCRIPT_KEYS:
            for k in config.default.keys.get(key, ()):
                drop(k.source, k.start, k.end)
    out: list[str] = []
    unsettled: set[int] = set()
    flow_settled = True
    if config.workflow is not None and jobs:
        body, flow_settled = _render(config, config.workflow.keys, lines)
        flow_settled = flow_settled and config.workflow.unresolved is None
        out += ["workflow:", *body]
    for n, job in enumerate(jobs):
        start = len(out)
        body, settled = _render(config, job.keys, lines)
        out += [f"job{n}:", *body]
        if not (settled and flow_settled) or job.unresolved is not None:
            unsettled.update(range(start, len(out)))
    texts = tuple("\n".join("" if i in blank[s] else line for i, line in enumerate(src))
                  if s in blank else config.sources[s] for s, src in enumerate(lines))
    return MergedView(texts, "\n".join(out), frozenset(unsettled))
