"""The supportive vocabulary for user-facing output (issue #743, parent #740).

Every report reads as a readiness snapshot, never a verdict on the people who built
the repository. This module is the one source for the words rendered prose leaves
out, the old band labels that stay retired, and the opening line every report
section starts with. ``tests/test_vocabulary_guard.py`` renders the fleet report,
the team next-steps pages, the heat map, the layer bars and the /assess
deterministic report from source and fails, naming the word and where it was
found, when one of them reappears.

Only prose is checked: fenced code, backticked spans, URLs (bare or as a Markdown link
target) and identifiers that contain an underscore are left out (``prose``), and the
retired label "AI-Native" never matches as part of the product name ``ai-native-toolkit``, so machine field names, JSON keys, finding
names and signal paths (``lying_map``, ``instruction_claims.failed``) stay as they
are. Stdlib only.
"""
from __future__ import annotations

import re

#: Critical words and the supportive wording used in their place. Matched as whole
#: words, case-insensitive, so "fail" does not match "failure"; each inflection a
#: renderer could produce is listed on its own.
BANNED_WORDS: dict[str, str] = {
    "tangled": "name the measured structure (cross-package edges, modularity)",
    "penalty": "name the size or share the grade reads",
    "weakest": "the row with the most room to grow",
    "weak": "room to grow",
    "lying": "stale, or reads better than it measures",
    "missing": "not measured yet, or not in place yet",
    "fail": "stop, or turn the job red",
    "fails": "stops",
    "failed": "could not be scanned, or did not run",
    "failing": "erroring",
    "failure": "an error, or a gap",
}

#: The old maturity band labels (#740, #747): the Step names replace them.
RETIRED_LABELS: tuple[str, ...] = ("Not Ready", "Basic", "Solid", "AI-Native")

#: Every section of a fleet ``report.md`` and of a team next-steps page opens with a
#: line that starts with this prefix and says what is already in place, before any
#: next step.
IN_PLACE = "In place:"

_FENCE = re.compile(r"```.*?```", re.S)
_TICKS = re.compile(r"`[^`\n]*`")
_IDENT = re.compile(r"\S*_\S*")
# A URL is an address, not prose: a bare one, or a Markdown link target ``](...)``.
_URL = re.compile(r"\]\([^)\s]*\)|<?https?://[^\s)>]+>?")

#: "AI-Native" is also the product's own name in ``ai-native-toolkit``, so the retired
#: label matches (in any case) only as a whole token not followed by ``-toolkit``.
_AI_NATIVE = re.compile(r"(?<![\w-])AI-Native(?!-toolkit)(?![\w-])", re.I)


def prose(text: str) -> str:
    """``text`` with fenced code, backticked spans, URLs and underscored identifiers blanked."""
    return _IDENT.sub(" ", _URL.sub(" ", _TICKS.sub(" ", _FENCE.sub(" ", text))))


def _pattern(term: str) -> re.Pattern[str]:
    if term == "AI-Native":
        return _AI_NATIVE
    return re.compile(r"\b" + re.escape(term) + r"\b", re.I)


_TERMS = tuple((term, _pattern(term)) for term in (*BANNED_WORDS, *RETIRED_LABELS))


def banned_hits(text: str) -> list[tuple[str, int, str]]:
    """Each (term, 1-based line, line text) where a banned word or retired label is in the prose."""
    hits: list[tuple[str, int, str]] = []
    for number, line in enumerate(prose(text).splitlines(), start=1):
        for term, pattern in _TERMS:
            if pattern.search(line):
                hits.append((term, number, line.strip()))
    return hits


def opening_lines(markdown: str, level: str = "#") -> list[tuple[str, str]]:
    """(heading, first non-blank line under it) for each heading of at least ``level``.

    ``level`` is the heading prefix to match: ``"#"`` matches every heading, ``"##"``
    the ``## `` headings only (not ``###``). A heading followed directly by another
    heading, or by nothing, pairs with an empty string.
    """
    lines = markdown.splitlines()
    pairs: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        marks = len(line) - len(line.lstrip("#"))
        if not marks or not line[marks:].startswith(" "):
            continue
        if level != "#" and marks != len(level):
            continue
        following = next((nxt for nxt in lines[i + 1:] if nxt.strip()), "")
        pairs.append((line, "" if following.startswith("#") else following))
    return pairs
