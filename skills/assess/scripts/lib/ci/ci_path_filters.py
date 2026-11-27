"""Instruction files that no pull request check sees: the ``paths`` filters of PR workflows (issue #857).

A hazard of the default maturity model reads this through the ``ci_hazards`` block
(``lib.ci.ci_hazards``). When every GitHub Actions workflow that runs a check on pull requests
filters an instruction file out, a change to that file reaches the main branch unchecked, while
the ``instructions`` Step gate trusts it. Nothing is executed; the workflow text is read line by
line with the ``ci_masking`` indentation model.

- **The files.** The tracked instruction files at the fixed locations
  (``lib.instructions.instruction_files.INSTRUCTION_FILE_PATHS``: root ``CLAUDE.md``,
  ``AGENTS.md``, ``GEMINI.md``, ``.cursorrules``, the ``.github/`` and ``docs/`` variants).
- **The workflows.** Every workflow whose ``on:`` names ``pull_request`` or
  ``pull_request_target``. The count is measured only when at least one of them runs a test or
  lint command (``lib.ci.ci_checks.check_commands``), as for ``skip_labels``. A file is
  unchecked only when every PR workflow filters it out, including one whose check this reader
  does not see (a reusable workflow or a script it does not follow), so such a workflow clears it.
- **A filter.** Each such event's ``paths:`` or ``paths-ignore:`` list, read as GitHub does:
  ``*`` matches within one path segment, ``**`` across segments (``**/`` also at the root), and
  a ``!`` pattern re-includes or re-excludes, the last match winning. ``paths:`` lets a file
  through when its last matching pattern is not negated; ``paths-ignore:`` stops it when its last
  matching pattern is not negated. An event with no filter lets every file through.
- **Precision first.** A workflow lets a file through when any of its PR events does. An event
  this reader cannot read (an ``on:`` flow mapping, a filter that is empty or not a plain list, ``paths``
  beside ``paths-ignore``, which GitHub rejects, or a pattern with ``?``, ``+`` or ``[``) lets
  every file through, so the hazard is never raised on a filter it did not read. A trailing
  ``#`` comment on a list item is dropped first. A PR workflow that also runs on
  ``merge_group`` lets every file through, since a merge queue run takes no ``paths`` filter.

``unchecked_instructions(workflows, tracked)`` returns the count of instruction files that every
PR workflow filters out, the instruction files themselves and the workflows read, or None
(``no_pr_checks`` in the block) when no workflow runs a check on pull requests. Tests:
``tests/test_instruction_paths_hazard.py``.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from lib.ci.ci_checks import check_commands
from lib.ci.ci_masking import IndentTree, parse_lines, strip_comment
from lib.ci.ci_model_jobs import _list, _on_value
from lib.ci.ci_triggers import workflow_events
from lib.instructions.instruction_files import INSTRUCTION_FILE_PATHS

PR_EVENTS = frozenset({"pull_request", "pull_request_target"})
_UNREAD_GLOB = re.compile(r"[?+\[\]]")


class PathFilter(NamedTuple):
    key: str  # "paths" or "paths-ignore"
    patterns: tuple[re.Pattern[str], ...]
    negated: tuple[bool, ...]


def glob_regex(pattern: str) -> re.Pattern[str] | None:
    """A GitHub filter pattern as a full-path regex, or None for one this reader does not read."""
    if _UNREAD_GLOB.search(pattern) or not pattern:
        return None
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _filter(key: str, raw: list[str]) -> PathFilter | None:
    patterns: list[re.Pattern[str]] = []
    negated: list[bool] = []
    for value in raw:
        item = strip_comment(value).strip("'\"")  # a block item keeps its trailing comment
        if not item or "#" in item or "'" in item or '"' in item:
            return None
        neg = item.startswith("!")
        regex = glob_regex(item[1:] if neg else item)
        if regex is None:
            return None
        patterns.append(regex)
        negated.append(neg)
    return PathFilter(key, tuple(patterns), tuple(negated))


def admits(found: PathFilter | None, path: str) -> bool:
    """Whether a PR event with this filter (None: no filter, or one not read) runs on ``path``."""
    if found is None:
        return True
    last: bool | None = None  # the negation of the last matching pattern, None when none matches
    for regex, neg in zip(found.patterns, found.negated):
        if regex.fullmatch(path):
            last = neg
    if found.key == "paths":
        return last is False
    return last is not False


def pr_filters(text: str) -> list[PathFilter | None] | None:
    """One filter per PR event of a GitHub workflow (None for an event with no filter or one not
    read), or None when the workflow names no PR event."""
    named = set(workflow_events(text) or [])
    events = named & PR_EVENTS
    if not events:
        return None
    if "merge_group" in named:  # a merge queue run takes no paths filter: it checks every file
        return [None]
    tree = IndentTree(parse_lines(text.splitlines()))
    on = next((i for i, line in enumerate(tree.lines)
               if line.structural and line.level == 0 and _on_value(line.key, line.value) is not None), None)
    if on is None:
        return [None]
    keyed = {tree.lines[k].key: k for k in tree.children(on) if not tree.lines[k].dash}
    out: list[PathFilter | None] = []
    for name in sorted(events):
        if name not in keyed or tree.lines[keyed[name]].value not in ("", "null", "~"):
            out.append(None)
            continue
        filters = {tree.lines[k].key: k for k in tree.children(keyed[name])}
        keys = [key for key in ("paths", "paths-ignore") if key in filters]
        raw = _list(tree, filters[keys[0]]) if len(keys) == 1 else None
        out.append(_filter(keys[0], raw) if raw else None)  # an empty list filters nothing read
    return out


def unchecked_instructions(workflows: dict[str, str], tracked: frozenset[str]) -> tuple[int | None, list[str], list[str]]:
    """The count of instruction files every PR check workflow filters out, those files and the
    workflows read for it (module docstring); a null count when no workflow checks pull requests."""
    read: list[str] = []
    checking = False
    files = [p for p in INSTRUCTION_FILE_PATHS if p in tracked]
    unchecked = set(files)
    for rel, text in sorted(workflows.items()):
        filters = pr_filters(text)
        if filters is None:
            continue
        read.append(rel)
        checking = checking or check_commands(text) > 0
        # any PR workflow that runs on the file clears it, a check seen or not: its check may sit
        # in a reusable workflow or a script this reader does not follow
        unchecked -= {p for p in files if any(admits(f, p) for f in filters)}
    if not checking:
        return None, [], []
    found = sorted(unchecked)
    return len(found), found, (read if found else [])
