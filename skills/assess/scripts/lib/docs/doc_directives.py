"""Detect a doc page that is only documentation-generator directives (#816).

An API reference page whose body is a mkdocstrings directive (``::: pkg.mod``)
is rendered from docstrings at build time, so it cannot drift from the code it
names. ``lib.docs.doc_staleness`` reads :func:`is_directive_only` to keep such a page
out of the staleness share and to report how many it set aside.

A page is directive-only when it has at least one directive and its other
content is small: at most ``max(PROSE_LINE_ALLOWANCE, directives)`` prose lines.
What counts:

- **Directive:** a line ``::: <dotted.identifier>`` outside a code fence. An
  indented ``options:`` line straight after it, and the lines indented deeper
  than that line (mkdocstrings options are YAML), belong to it. Any other
  indented line after a directive is prose.
- **Not prose:** blank lines, ATX headings, a leading YAML frontmatter block,
  HTML comments, and the lines inside code fences are ignored. Headings never
  count as prose, so a page of headings and one directive is directive-only, by
  design. A code fence counts as one prose line, since an example is hand-written.
- **Prose:** every other line.

Other ``:::`` syntaxes are not directives: a Docusaurus or VitePress admonition
(``:::note``, ``::: warning``, a name in ``ADMONITION_NAMES``) and a pandoc
fenced div with attributes (``::: {.class}``). A bare ``:::`` closer anywhere on
the page marks div syntax (``::: notes`` ... ``:::``), which mkdocstrings never
writes, so such a page is never directive-only. A hand-written page that embeds
one directive among its prose still reads as hand-written, so it stays measured.
Sphinx autodoc stubs (reST) are out of scope until reST docs are read. The read
is bounded to ``MAX_BYTES``; a longer page is never directive-only. Tests:
``tests/test_doc_directives.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

# Prose lines a directive page may carry beside its directives: an intro
# sentence, a setup link, a "see also" line.
PROSE_LINE_ALLOWANCE = 3
MAX_BYTES = 65536

_DIRECTIVE = re.compile(r"^:::\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s*$")
_FENCE = re.compile(r"^(```|~~~)")
ADMONITION_NAMES = frozenset({
    "note", "tip", "info", "important", "warning", "caution", "danger",
    "details", "success", "question", "failure", "bug", "example", "quote",
    "abstract", "hint", "attention", "error", "seealso", "todo",
})


def _strip_frontmatter(lines: list[str]) -> list[str]:
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return lines[i + 1:]
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def directive_counts(text: str) -> tuple[int, int, int]:
    """``(directive lines, prose lines, bare ::: closers)`` in a markdown page."""
    directives = prose = closers = 0
    fence: str | None = None
    in_comment = after_directive = False
    options_indent: int | None = None
    for line in _strip_frontmatter(text.splitlines()):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if in_comment:
            in_comment = "-->" not in stripped
            continue
        if options_indent is not None:
            if not stripped or _indent(line) > options_indent:
                continue
            options_indent = None
        if after_directive and stripped:
            after_directive = False
            if _indent(line) > 0 and stripped.startswith("options:"):
                options_indent = _indent(line)
                continue
        if stripped == ":::":
            closers += 1
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            continue
        fence_match = _FENCE.match(stripped)
        if fence_match:
            fence = fence_match.group(1)
            prose += 1
            continue
        match = _DIRECTIVE.match(line)
        if match and match.group(1).lower() not in ADMONITION_NAMES:
            directives += 1
            after_directive = True
            continue
        prose += 1
    return directives, prose, closers


def is_directive_only(doc: Path) -> bool:
    """True when ``doc`` is a markdown page of generator directives (module docstring)."""
    if doc.suffix.lower() not in (".md", ".markdown"):
        return False
    try:
        raw = doc.read_bytes()
    except OSError:
        return False
    if len(raw) > MAX_BYTES:
        return False
    directives, prose, closers = directive_counts(raw.decode("utf-8", errors="replace"))
    return (directives > 0 and not closers
            and prose <= max(PROSE_LINE_ALLOWANCE, directives))
