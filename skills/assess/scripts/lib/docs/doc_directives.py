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

A reST page (``.rst``, or a Sphinx ``.txt`` source, #840) counts the same way
with Sphinx autodoc directives (:func:`rst_directive_counts`): a line
``.. automodule:: x`` (or ``autoclass``, ``autofunction``, ``autosummary`` and
the rest of ``AUTODOC_DIRECTIVES``) is a directive, and the lines indented under
it (``:members:``) belong to it. Section titles and their adornment lines,
blank lines, comments, ``.. _label:`` targets and ``.. module::`` /
``.. currentmodule::`` are ignored; any other directive (``.. note::``) counts as
one prose line, its body included. The read is bounded to ``MAX_BYTES``; a
longer page is never directive-only. Tests: ``tests/test_doc_directives.py``.
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
AUTODOC_DIRECTIVES = frozenset({
    "automodule", "autoclass", "autofunction", "autoexception", "automethod",
    "autoattribute", "autodata", "autodecorator", "autoproperty", "autosummary",
})
_RST_IGNORED_DIRECTIVES = frozenset({"module", "currentmodule"})
_RST_DIRECTIVE = re.compile(r"^\.\.\s+(?:py:)?([\w-]+)::")
_RST_ADORNMENT = re.compile(r"^([=\-`:'\"~^_*+#<>.])\1{2,}\s*$")
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


def rst_directive_counts(text: str) -> tuple[int, int, int]:
    """``(autodoc directives, prose lines, 0)`` in a reST page (module docstring)."""
    lines = text.splitlines()
    directives = prose = 0
    in_block = False  # inside an indented directive or comment body
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _RST_ADORNMENT.match(stripped):
            continue
        if in_block and _indent(line) > 0:
            continue
        in_block = False
        if i + 1 < len(lines) and _RST_ADORNMENT.match(lines[i + 1].strip()):
            continue  # a section title
        if stripped.startswith(".."):
            in_block = True
            match = _RST_DIRECTIVE.match(stripped)
            if match is None:  # a comment or a `.. _label:` target
                continue
            name = match.group(1).lower()
            if name in AUTODOC_DIRECTIVES:
                directives += 1
            elif name not in _RST_IGNORED_DIRECTIVES:
                prose += 1
            continue
        prose += 1
    return directives, prose, 0


def is_directive_only(doc: Path) -> bool:
    """True when ``doc`` is a markdown page of generator directives (module docstring)."""
    suffix = doc.suffix.lower()
    if suffix not in (".md", ".markdown", ".rst", ".txt"):
        return False
    try:
        raw = doc.read_bytes()
    except OSError:
        return False
    if len(raw) > MAX_BYTES:
        return False
    count = rst_directive_counts if suffix in (".rst", ".txt") else directive_counts
    directives, prose, closers = count(raw.decode("utf-8", errors="replace"))
    return (directives > 0 and not closers
            and prose <= max(PROSE_LINE_ALLOWANCE, directives))
