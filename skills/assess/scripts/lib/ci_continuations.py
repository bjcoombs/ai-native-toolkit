"""Join a CI line continued with ``\\`` onto its first line (issue #799).

``lib.ci_checks`` classifies each CI line on its own, so a command split over lines
(``mvn ${MVN_ARGS} -T8 \\`` / ``-DskipTests \\`` / ``verify``) used to be read from its first
line only, and the goal on the last line was never seen. ``join_continued(text)`` returns the
text with each such command on its first physical line and the lines it continued onto left
blank, so every line keeps its index: the masking (``lib.ci_masking``), trigger
(``lib.ci_triggers``) and count readers still line up. The join is idempotent.

A line continues when it ends in an odd run of backslashes (``\\\\`` is a literal backslash)
and is not a comment. It takes the next line only when that line is not blank and sits at
the first line's indent or deeper: a shallower line is the next YAML key, never shell. A
folded block scalar (``run: >``) is joined by ``lib.ci_masking`` itself, not here.

Inward-only imports: stdlib only. Tests: ``tests/test_ci_continuations.py``.
"""
from __future__ import annotations


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _continues(line: str) -> bool:
    """Ends in an odd run of backslashes and is not a comment line."""
    body = line.rstrip()
    if body.lstrip().startswith("#"):
        return False
    run = len(body) - len(body.rstrip("\\"))
    return run % 2 == 1


def join_continued(text: str) -> str:
    """``text`` with each ``\\``-continued command on its first line (module docstring)."""
    lines = text.splitlines()
    out = list(lines)
    i = 0
    while i < len(lines):
        if not _continues(lines[i]):
            i += 1
            continue
        joined, depth, j = lines[i].rstrip(), _indent(lines[i]), i + 1
        while _continues(joined) and j < len(lines) and lines[j].strip() and _indent(lines[j]) >= depth:
            joined = joined[:-1].rstrip() + " " + lines[j].strip()
            out[j] = ""
            j += 1
        out[i] = joined
        i = j
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")
