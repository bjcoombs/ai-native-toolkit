"""A strict reader for small YAML config files: block mappings and lists into plain values (#856).

The deterministic core carries no YAML library (``lib.pack_yaml``). The merge and review rule
files ``lib.ci.review_rules`` reads (Prow ``OWNERS`` and ``OWNERS_ALIASES``, policy-bot's
``.policy.yml``, a Mergify config) need lists and nested mappings, which ``lib.pack_yaml``
leaves unopened, so ``parse(text)`` reads the subset those files use into dicts, lists and
scalars:

- block mappings and block lists (a list may sit at its key's own column), a list item that
  opens a mapping (``- name: x``), and one-line flow lists and mappings (``[a, b]``,
  ``{count: 1}``), nested;
- plain, ``'single'`` and ``"double"`` quoted scalars on one line. A plain scalar reads as an
  ``int`` when it is all digits, a ``bool`` for ``true`` / ``false`` (any case), ``None`` for
  ``null`` / ``~`` or an empty value, else as the string; a quoted scalar is always a string;
- a block scalar (``|``, ``>``) is read past and holds ``""``: none of these files keeps a rule
  in one;
- comments (``lib.pack_yaml.strip_comment``) and one leading ``---``.

Anything else raises ``lib.pack_yaml.YamlError`` with its code, so a caller reads the file as
unsettled, never as empty: an anchor, alias, tag, merge key or complex key, a flow collection
or quoted scalar that spans lines, a plain scalar continued on the next line, a second
document or a directive (``unsupported_yaml``); a tab in the indentation, a duplicate key, or
a line no open block owns (``unparsable_file``). Tests: ``tests/test_review_rules.py``.
"""
from __future__ import annotations

import json
import re
from typing import Union

from lib.pack_yaml import UNPARSABLE, UNSUPPORTED, YamlError, quote_opens, quoted_end, split_key, strip_comment

Value = Union[str, int, bool, None, "list[Value]", "dict[str, Value]"]
Row = tuple[int, str]

_INT = re.compile(r"[-+]?\d+")
_NULLS = frozenset({"", "~", "null", "Null", "NULL"})


def _rows(text: str) -> list[Row]:
    rows: list[Row] = []
    started = False
    for raw in text.lstrip("﻿").splitlines():
        line = strip_comment(raw)
        if not line.strip():
            continue
        lead = line[:len(line) - len(line.lstrip(" \t"))]
        if "\t" in lead:
            raise YamlError(UNPARSABLE)
        body = line.strip()
        if not lead and (body in ("---", "...") or body.startswith(("--- ", "%"))):
            if started or body != "---":
                raise YamlError(UNSUPPORTED)
            continue
        started = True
        rows.append((len(lead), body))
    return rows


def _split_flow(inner: str) -> list[str]:
    """The top-level comma-separated parts of a flow collection's inside."""
    parts: list[str] = []
    depth, start, i = 0, 0, 0
    while i < len(inner):
        ch = inner[i]
        if ch in "'\"" and quote_opens(inner, i):
            end = quoted_end(inner[i:])
            if end < 0:
                raise YamlError(UNSUPPORTED)
            i += end
            continue
        depth += (ch in "[{") - (ch in "]}")
        if depth < 0:
            raise YamlError(UNPARSABLE)
        if ch == "," and depth == 0:
            parts.append(inner[start:i])
            start = i + 1
        i += 1
    if depth:
        raise YamlError(UNSUPPORTED)  # a flow collection that runs onto another line
    parts.append(inner[start:])
    if not parts[-1].strip():
        parts.pop()
    return parts


def _flow(text: str) -> Value:
    closer = "]" if text[0] == "[" else "}"
    if not text.endswith(closer):
        raise YamlError(UNSUPPORTED)
    parts = [p.strip() for p in _split_flow(text[1:-1])]
    if any(not p for p in parts):
        raise YamlError(UNPARSABLE)
    if closer == "]":
        return [_scalar(p) for p in parts]
    out: dict[str, Value] = {}
    for part in parts:
        key, value = split_key(part)
        if key in out:
            raise YamlError(UNPARSABLE)
        out[key] = _scalar(value)
    return out


def _scalar(text: str) -> Value:
    """The value of one-line scalar or flow text."""
    text = text.strip()
    if text[:1] in ("&", "*", "!", "%", "@", "`", "?"):
        raise YamlError(UNSUPPORTED)
    if text[:1] in ("[", "{"):
        return _flow(text)
    if text[:1] in ("'", '"'):
        if quoted_end(text) != len(text):
            raise YamlError(UNSUPPORTED)
        if text[0] == "'":
            return text[1:-1].replace("''", "'")
        try:
            return str(json.loads(text))
        except ValueError as exc:
            raise YamlError(UNSUPPORTED) from exc
    if text in _NULLS:
        return None
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if _INT.fullmatch(text):
        return int(text)
    if ": " in text or text.endswith(":"):
        raise YamlError(UNPARSABLE)
    return text


class _Reader:
    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows
        self.i = 0

    def peek(self) -> Row | None:
        return self.rows[self.i] if self.i < len(self.rows) else None

    def node(self, indent: int) -> Value:
        """The block that opens at the next row, whose column must be ``indent``."""
        row = self.peek()
        if row is None or row[0] != indent:
            raise YamlError(UNPARSABLE)
        if row[1] == "-" or row[1].startswith("- "):
            return self.sequence(indent)
        return self.mapping(indent, row[1])

    def child(self, owner: int, allow_list_at_owner: bool) -> Value:
        """The value of a key or item whose text is empty: the deeper block below, if any."""
        row = self.peek()
        if row is None:
            return None
        if row[0] > owner:
            return self.node(row[0])
        if allow_list_at_owner and row[0] == owner and (row[1] == "-" or row[1].startswith("- ")):
            return self.sequence(owner)
        return None

    def skip_block_scalar(self, owner: int) -> None:
        while (row := self.peek()) is not None and row[0] > owner:
            self.i += 1

    def value(self, owner: int, text: str, allow_list_at_owner: bool) -> Value:
        if not text:
            return self.child(owner, allow_list_at_owner)
        if text[0] in "|>":
            self.skip_block_scalar(owner)
            return ""
        value = _scalar(text)
        row = self.peek()
        if row is not None and row[0] > owner:
            raise YamlError(UNSUPPORTED)  # a plain scalar continued on the next line
        return value

    def mapping(self, indent: int, first: str | None = None) -> dict[str, Value]:
        out: dict[str, Value] = {}
        while (row := self.peek()) is not None and row[0] == indent:
            text = first if first is not None and not out else row[1]
            if text == "-" or text.startswith("- "):
                raise YamlError(UNPARSABLE)
            self.i += 1
            key, rest = split_key(text)
            if key in out:
                raise YamlError(UNPARSABLE)
            out[key] = self.value(indent, rest, allow_list_at_owner=True)
        row = self.peek()
        if row is not None and row[0] > indent:
            raise YamlError(UNPARSABLE)
        return out

    def sequence(self, indent: int) -> list[Value]:
        out: list[Value] = []
        while (row := self.peek()) is not None and row[0] == indent and (
                row[1] == "-" or row[1].startswith("- ")):
            rest = row[1][1:].lstrip(" ")
            column = indent + len(row[1]) - len(rest)
            if rest and rest[0] not in "'\"[{" and (": " in rest or rest.endswith(":")):
                self.rows[self.i] = (column, rest)  # ``- key: v`` opens a mapping at the key's column
                out.append(self.mapping(column))
                continue
            self.i += 1
            out.append(self.value(indent, rest, allow_list_at_owner=False))
        return out


def parse(text: str) -> Value:
    """The value of a YAML document in the subset above; ``YamlError`` on anything else."""
    rows = _rows(text)
    if not rows:
        return None
    reader = _Reader(rows)
    column, body = rows[0]
    if column:
        raise YamlError(UNPARSABLE)
    root: Value
    if body[:1] in ("[", "{", "&", "*", "!"):
        root = _scalar(body)
        reader.i = 1
    else:
        root = reader.node(0)
    if reader.peek() is not None:
        raise YamlError(UNPARSABLE)
    return root
