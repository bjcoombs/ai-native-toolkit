"""The words an AI-disclosure check is read by: the read, the marker, the names and the two acts (issue #1599).

Shared by the per-step readers ``lib.ci.disclosure_shell`` (shell, through ``shlex``) and
``lib.ci.disclosure_script`` (Python through ``ast``, JavaScript through a small tokenizer),
which relate these terms structurally; ``lib.ai_governance`` dispatches a step's code to them.

- ``is_read(text)``: the text reads the pull request body or the commit messages and trailers
  (``pull_request.body``, ``gh pr view --json body``, ``git log`` with a body or trailer format,
  ``git interpret-trailers``, the pull request commits API). It is applied to one node of a
  parsed program (a shell word, a Python expression, a JavaScript statement), never to a line.
- ``has_marker(text)``: the text names an AI disclosure or attribution marker: a tool or model
  name, ``AI`` as a capitalised word, or an ``Assisted-by`` or ``Generated-by`` trailer.
  ``cursor`` is not one, because GraphQL pagination uses the word. It is applied only to the
  pattern operand of a test (a grep pattern, a string literal or a ``/regex/``).
- ``refers(word, names)``: a shell word reads the body itself or names what holds it, a
  variable (``$BODY``, ``${BODY}``) or a file (``body.json``) in ``names``.
- ``EXIT`` and ``LABEL``: the two acts a reader reports, a non-zero exit (the run turns red) and
  a label added to the pull request. A step that cannot turn the run red counts only on ``LABEL``.

Inward-only imports: stdlib only. Tests: ``tests/test_ai_governance_condition.py``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

EXIT = "exit"
LABEL = "label"

_I = re.IGNORECASE
_READS = re.compile(r"pull_request\.body|gh\s+pr\s+view\b.*--json\s+[\w,]*body|interpret-trailers"
                    r"|%\(trailers|git\s+log\b.*(?:%B|%b|--format=(?:full|raw|medium))"
                    r"|pulls\.listCommits|pull_request\.commits_url|/pulls/[^\s/]+/commits", _I | re.DOTALL)
_MARKERS = re.compile(r"\bAI\b|(?i:\b(?:llm|copilot|claude|chatgpt|gpt-?\d|gemini|codex"
                      r"|assisted-by|generated-by|ai-assisted|ai-generated)\b)")
_IDENT = re.compile(r"[A-Za-z_]\w*\Z")


def is_read(text: str) -> bool:
    return _READS.search(text) is not None


def has_marker(text: str) -> bool:
    return _MARKERS.search(text) is not None


def refers(word: str, names: Iterable[str]) -> bool:
    """Whether a shell word reads the body itself or names a variable or file in ``names``."""
    if is_read(word):
        return True
    for name in names:
        if _IDENT.match(name) and re.search(rf"\$\{{?{name}\b", word):
            return True
        if word == name or word.endswith("/" + name):
            return True
    return False
