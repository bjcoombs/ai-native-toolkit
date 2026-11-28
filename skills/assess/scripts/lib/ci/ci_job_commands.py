"""The commands of a CI job, read the way the CI-step readers share (issues #852, #854).

Moved out of ``lib.ci.docs_kept_true`` so the docs-kept-true reader and the repo-guards reader
(``lib.ci.repo_guards``) split a shell line, read through a wrapper and pick a job's shell
bodies alike:

- ``commands(text)`` yields each command of one shell line (split on ``&&``, ``||``, ``;``
  and ``|``) with ``masked``: a ``||`` fallback that does not re-raise (``exit 1``,
  ``exit $rc``, ``false``) before the next ``;`` or ``&&`` follows it, so the command cannot
  fail the job.
- ``program(words)`` is ``words`` from the program a wrapper runs (``uv run --locked pytest``
  reads ``pytest``; ``python -m doctest`` reads ``doctest``), the wrapper's own options
  skipped (#1589, ``lib.ci.check_command.WRAPPER_FLAGS``).
- ``installs(words)`` is an install or dependency sync (``pip install``, ``uv sync``,
  ``npm ci``): such a command runs nothing it installs.
- ``job_lines(text, gitlab)`` is each job's unmasked shell lines in order (#775,
  ``lib.ci.ci_masking.mask_lines``): a GitHub ``run:`` body, or a GitLab ``script:`` or
  ``before_script:`` body. A github-script ``with: script:`` is JavaScript, and GitLab ignores
  an ``after_script:`` failure when it sets the job status, so neither is read.

Inward-only imports; never an orchestrator. Tests: ``tests/test_docs_kept_true.py`` and
``tests/test_repo_guards.py``.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TypedDict

from lib.ci.check_command import WRAPPER_FLAGS
from lib.ci.ci_masking import IndentTree, mask_lines, parse_lines
from lib.instructions.command_resolver import command_words

OPERATOR = re.compile(r"(&&|\|\||;|\|)")
RERAISE = re.compile(r"(?:^|[;{(]|&&|\|\|)\s*(?:exit\s+(?:[1-9]\d*|\"?\$)|false\b)")
GITHUB_BODY = frozenset({"run"})   # a github-script ``with: script:`` is JavaScript, not shell
GITLAB_BODY = frozenset({"script", "before_script"})  # GitLab ignores after_script failures
WRAPPERS = frozenset({"uv", "poetry", "pdm", "hatch", "pipenv", "rye", "python", "python3", "run", "exec",
                      "uvx", "pipx", "npx", "bunx"})


class Cmd(TypedDict):
    words: list[str]
    masked: bool  # a ``||`` fallback that does not re-raise follows it


def commands(text: str) -> Iterator[Cmd]:
    """Each command of one shell line, with whether a passing ``||`` fallback follows it."""
    parts = OPERATOR.split(text)
    for i in range(0, len(parts), 2):
        words = command_words(parts[i].strip().strip("(){} "))
        if not words:
            continue
        rest = parts[i + 1:]
        masked = bool(rest) and rest[0] == "||" and not RERAISE.search(fallback(rest[1:]) + " ")
        yield Cmd(words=words, masked=masked)


def fallback(rest: list[str]) -> str:
    """The ``||`` fallback's text: up to the next ``;`` or ``&&``, braces kept together."""
    out: list[str] = []
    depth = 0
    for part in rest:
        if part in (";", "&&") and depth <= 0:
            break
        depth += part.count("{") + part.count("(") - part.count("}") - part.count(")")
        out.append(part)
    return " ".join(out)


def program(words: list[str]) -> list[str]:
    """``words`` from the program a wrapper runs (module docstring). An option is skipped only
    after a wrapper word, so a program's own flags are kept; a valued option with no program
    after it leaves nothing."""
    i = 0
    while i < len(words) - 1:
        word = words[i]
        if word in WRAPPERS or word == "-m":
            i += 1
        elif i and word.startswith("-"):
            i += 2 if word in WRAPPER_FLAGS else 1
        else:
            break
    return words[i:]


def installs(words: list[str]) -> bool:
    """An install or dependency sync (``pip install``, ``python -m pip install``, ``uv sync``, ``npm ci``)."""
    prog = program(words)
    return ("install" in words[:3] or "install" in prog[:3] or words[:2] == ["npm", "ci"]
            or (words[0] in ("uv", "poetry", "pdm", "rye") and prog[:1] == ["sync"]))


def job_lines(text: str, gitlab: bool) -> list[list[str]]:
    """Each job's unmasked shell lines in order (module docstring)."""
    keys = GITLAB_BODY if gitlab else GITHUB_BODY
    lines = text.splitlines()
    unmasked, _unsure = mask_lines(text)
    tree = IndentTree(parse_lines(lines))
    spans: dict[int, list[str]] = {}
    for i, line in enumerate(unmasked):
        if line.strip() and tree.lines[tree.body(i)].key in keys:
            spans.setdefault(tree.job(i).start, []).append(line)
    return list(spans.values())
