"""Read the ``imports:`` list of a gh-aw source's YAML frontmatter, failing closed (#1602).

The deterministic core carries no YAML library (``lib.pack_yaml``), so this module reads the
frontmatter as YAML structure rather than matching lines, and raises
``lib.pack_yaml.YamlError`` on anything it does not follow. A caller treats a raise as an
unread source, never as one with no imports.

- **Top level.** Every non-blank line at column 0 opens a ``key:`` (``lib.pack_yaml
  .split_key``); a sequence item at column 0 belongs to the key above it with an empty value.
  A flow value (``[``, ``{``) or a quoted value is followed across lines to its close. A line
  no key owns, a directive, a merge key, an anchor, alias or tag on ``imports``, and a second
  ``imports`` key raise.
- **Forms GitHub also accepts (#1608).** A tab after a key's colon reads as a space; a
  ``...`` document-end line ends the frontmatter, and any text after it raises; ``imports:
  null`` (or ``~``) with nothing under it reads as no imports.
- **The value.** ``imports:`` holds a flow list (on its own line or the next, over one line or
  several) or a block list. An item is a scalar (plain, ``'single'`` or ``"double"`` quoted;
  a comma inside quotes stays in the path) or a mapping, block or flow, whose ``path`` or
  ``uses`` key names the file. Any other key of a mapping is read past. A nested list, an
  empty item, a mapping with neither key, a multi-line plain scalar and a scalar the reader
  cannot decode raise.

``imports(front)`` returns the raw item strings in order; ``lib.agents.agent_instruction_rewrite
.gh_aw_imports`` resolves them. Tests: ``tests/test_gh_aw_imports.py``.
"""
from __future__ import annotations

import json

from lib.pack_yaml import UNPARSABLE, UNSUPPORTED, YamlError, quote_opens, quoted_end, split_key as _split_key
from lib.pack_yaml import strip_comment

IMPORT_KEYS = ("path", "uses")
NULLS = ("~", "null", "Null", "NULL")
DOCUMENT_END = "..."
Row = tuple[int, str]


def split_key(text: str) -> tuple[str, str]:
    """``lib.pack_yaml.split_key``, also taking a tab after an unquoted key's colon, as YAML does."""
    if text[:1] not in ("'", '"'):
        tab, space = text.find(":\t"), text.find(": ")
        if tab >= 0 and (space < 0 or tab < space):
            text = f"{text[:tab]}: {text[tab + 2:]}"
    return _split_key(text)


def _document(lines: list[str]) -> list[str]:
    """The lines before a ``...`` document-end line; any text after one raises."""
    if DOCUMENT_END not in lines:
        return lines
    end = lines.index(DOCUMENT_END)
    if any(line.strip() for line in lines[end + 1:]):
        raise YamlError(UNSUPPORTED)  # a second document, or text past the end
    return lines[:end]


def _indent(line: str) -> int:
    lead = line[:len(line) - len(line.lstrip(" \t"))]
    if "\t" in lead:
        raise YamlError(UNPARSABLE)
    return len(lead)


def _depth(text: str) -> int:
    """The net bracket depth ``text`` opens, skipping quoted scalars; an unclosed quote raises."""
    depth, i = 0, 0
    while i < len(text):
        past = _skip_quoted(text, i)
        if past > i:
            i = past
            continue
        depth += (text[i] in "[{") - (text[i] in "]}")
        i += 1
    return depth


def _entries(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """``(key, value, body lines)`` for each top-level key of the frontmatter."""
    entries: list[tuple[str, str, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        if _indent(line) or line.startswith("-"):
            raise YamlError(UNPARSABLE)  # a line no top-level key owns
        key, value = split_key(line)
        body: list[str] = []
        if value[:1] in ("'", '"') and quoted_end(value) < 0:
            while quoted_end(" ".join([value, *body])) < 0:
                if i == len(lines):
                    raise YamlError(UNPARSABLE)
                body.append(lines[i].strip())
                i += 1
        elif value[:1] in ("[", "{"):
            depth = _depth(value)
            while depth > 0:
                if i == len(lines):
                    raise YamlError(UNPARSABLE)
                depth += _depth(lines[i])
                body.append(lines[i])
                i += 1
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i])
                                  or (not value and lines[i].startswith("-"))):
            body.append(lines[i])
            i += 1
        entries.append((key, value, body))
    return entries


def _scalar(text: str) -> str:
    """The string a one-line scalar holds; anything that is not a plain or quoted string raises."""
    text = text.strip()
    if not text or text[0] in "[]{}&*!|>%@`?," or text == "-" or text.startswith("- "):
        raise YamlError(UNSUPPORTED)
    if text[0] in "'\"":
        if quoted_end(text) != len(text):
            raise YamlError(UNPARSABLE)
        if text[0] == "'":
            return text[1:-1].replace("''", "'")
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise YamlError(UNSUPPORTED) from exc
        return str(value)
    if ": " in text or ":\t" in text or text.endswith(":") or text in NULLS:
        raise YamlError(UNSUPPORTED)
    return text


def _skip_quoted(text: str, i: int) -> int:
    """The index past the quoted scalar opening at ``i``, or ``i`` when none opens there."""
    if text[i] not in "'\"" or not quote_opens(text, i):
        return i
    end = quoted_end(text[i:])
    if end < 0:
        raise YamlError(UNSUPPORTED)
    return i + end


def _split_flow(inner: str) -> list[str]:
    """The top-level comma-separated parts of a flow collection's inside."""
    if not inner.strip():
        return []
    parts: list[str] = []
    depth, start, i = 0, 0, 0
    while i < len(inner):
        past = _skip_quoted(inner, i)
        if past > i:
            i = past
            continue
        ch = inner[i]
        depth += (ch in "[{") - (ch in "]}")
        if ch == "," and depth == 0:
            parts.append(inner[start:i])
            start = i + 1
        i += 1
    parts.append(inner[start:])
    if not parts[-1].strip():
        parts.pop()  # a trailing comma; any other empty part raises in ``_scalar``
    return parts


def _flow_close(text: str) -> str:
    """The inside of the flow collection ``text`` opens, which must close at the text's end."""
    closer = "]" if text[0] == "[" else "}"
    depth, i = 0, 0
    while i < len(text):
        past = _skip_quoted(text, i)
        if past > i:
            i = past
            continue
        ch = text[i]
        depth += (ch in "[{") - (ch in "]}")
        if depth == 0:
            if ch != closer or text[i + 1:].strip():
                raise YamlError(UNPARSABLE)
            return text[1:i]
        i += 1
    raise YamlError(UNPARSABLE)


def _flow_mapping(text: str) -> list[str]:
    """The ``path``/``uses`` values of a flow mapping ``{path: a.md, inputs: {...}}``."""
    found: list[str] = []
    seen: set[str] = set()
    for part in _split_flow(_flow_close(text)):
        key, value = split_key(part.strip())
        if key in seen:
            raise YamlError(UNPARSABLE)
        seen.add(key)
        if key in IMPORT_KEYS:
            found.append(_scalar(value))
    if not found:
        raise YamlError(UNSUPPORTED)
    return found


def _flow_list(text: str) -> list[str]:
    """The items of a flow list ``[a.md, {path: b.md}]``."""
    if not text.startswith("["):
        raise YamlError(UNSUPPORTED)
    found: list[str] = []
    for part in _split_flow(_flow_close(text)):
        item = part.strip()
        found.extend(_flow_mapping(item) if item.startswith("{") else [_scalar(item)])
    return found


def _block_mapping(rows: list[Row]) -> list[str]:
    """The ``path``/``uses`` values of a block mapping item; other keys' values are read past."""
    column = rows[0][0]
    found: list[str] = []
    seen: set[str] = set()
    k = 0
    while k < len(rows):
        indent, text = rows[k]
        k += 1
        if indent != column:
            raise YamlError(UNPARSABLE)
        key, value = split_key(text)
        nested = k
        while k < len(rows) and rows[k][0] > column:
            k += 1
        if key in seen:
            raise YamlError(UNPARSABLE)
        seen.add(key)
        if key in IMPORT_KEYS:
            if k > nested:
                raise YamlError(UNSUPPORTED)
            found.append(_scalar(value))
    if not found:
        raise YamlError(UNSUPPORTED)
    return found


def _item(rest: str, column: int, children: list[Row]) -> list[str]:
    """The import paths one block list item holds."""
    if not rest:
        if not children or children[0][1].startswith("-"):
            raise YamlError(UNSUPPORTED)  # a null item or a nested list
        return _block_mapping(children)
    if rest[0] in "[{":
        text = " ".join([rest, *(t for _, t in children)])
        if rest[0] == "[":
            raise YamlError(UNSUPPORTED)
        return _flow_mapping(text)
    try:
        split_key(rest)
    except YamlError:
        if children:
            raise YamlError(UNSUPPORTED) from None  # a scalar over several lines
        return [_scalar(rest)]
    return _block_mapping([(column, rest), *children])


def _block_list(rows: list[Row]) -> list[str]:
    column = rows[0][0]
    found: list[str] = []
    k = 0
    while k < len(rows):
        indent, text = rows[k]
        k += 1
        if indent != column or not (text == "-" or text.startswith("- ")):
            raise YamlError(UNPARSABLE)
        rest = text[1:].lstrip(" ")
        start = k
        while k < len(rows) and rows[k][0] > column:
            k += 1
        found.extend(_item(rest, column + len(text) - len(rest), rows[start:k]))
    return found


def imports(front: str) -> list[str]:
    """The raw ``imports:`` items of a gh-aw frontmatter (module doc); ``YamlError`` when unread."""
    lines = _document([strip_comment(raw) for raw in front.lstrip("\ufeff").splitlines()])
    found = [(value, body) for key, value, body in _entries(lines) if key == "imports"]
    if not found:
        return []
    if len(found) > 1:
        raise YamlError(UNPARSABLE)
    value, body = found[0]
    rows = [(_indent(line), line.strip()) for line in body if line.strip()]
    if value in NULLS and not rows:
        return []
    if value:
        return _flow_list(" ".join([value, *(t for _, t in rows)]))
    if not rows:
        return []
    if rows[0][1].startswith("["):
        return _flow_list(" ".join(t for _, t in rows))
    return _block_list(rows)
