"""Frontmatter scope fields and glob matching for path-scoped instruction files.

Claude Code rules (``paths``), Copilot instructions (``applyTo``) and Cursor
rules (``globs``, ``alwaysApply``) name the files they apply to in YAML
frontmatter. This module reads those few keys with a line parser (no YAML
dependency: the deterministic core stays stdlib-only) and decides whether a
pattern matches any tracked file, so a rule whose glob matches nothing can be
reported. Matching is deliberately lenient - a pattern with no ``/`` matches a
basename at any depth, and a pattern matches a directory prefix - because a
false "dead glob" would send a maintainer to delete a working rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_FM_OPEN = "---"
_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_ITEM_RE = re.compile(r"^\s*-\s*(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def split_frontmatter(text: str) -> tuple[dict[str, list[str]], str]:
    """Split a leading ``---`` block into ``{key: [values]}`` and the body.

    A scalar value is a one-element list; a block list (``- item`` lines) or an
    inline list (``[a, b]``) is one element per item. Values are unquoted and
    otherwise raw: splitting a comma-separated scalar is the caller's call,
    because ``applyTo`` and ``globs`` use commas while a Claude ``paths`` item
    may hold a brace set (``*.{ts,tsx}``). No frontmatter, or an unclosed
    block, returns ``({}, text)``.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FM_OPEN:
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FM_OPEN)
    except StopIteration:
        return {}, text
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for raw in lines[1:end]:
        item = _ITEM_RE.match(raw)
        if item and current is not None and raw[:1] in (" ", "\t", "-"):
            value = _unquote(item.group(1))
            if value:
                fields[current].append(value)
            continue
        key = _KEY_RE.match(raw)
        if not key:
            continue
        current = key.group(1)
        value = key.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            fields[current] = [_unquote(v) for v in split_patterns(value[1:-1])]
        elif value:
            fields[current] = [_unquote(value)]
        else:
            fields[current] = []
    body = "\n".join(lines[end + 1:])
    return fields, body


def split_patterns(value: str) -> list[str]:
    """Split a comma-separated pattern list, keeping commas inside ``{...}``."""
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in value:
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return [p for p in (_unquote(s) for s in out) if p]


def scope_patterns(values: list[str]) -> list[str]:
    """Flatten frontmatter values into individual patterns (comma-split)."""
    out: list[str] = []
    for value in values:
        out.extend(split_patterns(value))
    return out


def is_true(values: list[str] | None) -> bool:
    """True for a frontmatter boolean ``true`` (``alwaysApply: true``)."""
    if not values:
        return False
    return values[0].strip().lower() == "true"


def expand_braces(pattern: str) -> list[str]:
    """Expand ``{a,b}`` brace sets (nested ones too) into plain patterns."""
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    depth = 0
    for end in range(start, len(pattern)):
        if pattern[end] == "{":
            depth += 1
        elif pattern[end] == "}":
            depth -= 1
            if depth == 0:
                break
    else:
        return [pattern]  # unbalanced: leave the brace literal
    head, tail = pattern[:start], pattern[end + 1:]
    out: list[str] = []
    for option in split_patterns(pattern[start + 1:end]) or [""]:
        out.extend(expand_braces(head + option + tail))
    return out


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile one brace-free glob into a lenient full-path regex."""
    pat = pattern.strip()
    while pat.startswith("./"):
        pat = pat[2:]
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    # gitignore semantics: a pattern with no slash matches at any depth.
    if "/" not in pat:
        pat = "**/" + pat
    out: list[str] = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[":
            close = pat.find("]", i + 1)
            if close == -1:
                out.append(re.escape(pat[i]))
                i += 1
            else:
                body = pat[i + 1:close].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = close + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    # A pattern naming a directory also covers the files below it.
    return re.compile("".join(out) + "(?:/.*)?")


def pattern_matches_any(pattern: str, paths: Iterable[str]) -> bool:
    """True when ``pattern`` matches at least one repo-relative POSIX path.

    A negated pattern (``!foo``) only removes files from a set, so it is never
    reported dead and counts as matching. A pattern that will not compile also
    counts as matching: an unreadable glob is not evidence of a dead one.
    """
    if not pattern.strip() or pattern.lstrip().startswith("!"):
        return True
    try:
        regexes = [_glob_regex(p) for p in expand_braces(pattern)]
    except re.error:
        return True
    return any(rx.fullmatch(path) for path in paths for rx in regexes)
