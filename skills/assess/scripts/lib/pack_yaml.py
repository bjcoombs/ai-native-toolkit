"""The YAML reader behind a pack's ``key_exists`` rule (#1273): block mappings only, no library.

The deterministic core carries no YAML library (``assess_core.py``'s inline dependencies are
``networkx`` and ``grimp``), and a pack check needs only one answer from a YAML file: does a
dotted key exist. ``parse`` reads the subset of YAML that answers it - nested block mappings -
into a tree of plain dicts whose leaves are ``LEAF`` (a scalar, null or a list) or ``OPAQUE``
(a flow collection or an alias, which the reader does not open). Anything else raises
``YamlError`` with a code, so the collector reports the check unavailable rather than false:

- ``unparsable_file``: not YAML at all (an indentation that matches no open mapping, a line
  that is neither a key nor a list item, a tab in the indentation);
- ``unsupported_yaml``: YAML this reader does not follow (a second document, a directive, a
  complex ``?`` key, a merge key ``<<``, a quoted or flow value that spans lines, a flow or
  alias document root).

``lookup(tree, key)`` walks a dotted key: True when it lands on any node, False when a segment
is missing or a leaf is in the way, and ``YamlError`` (``unsupported_yaml``) when the walk would
step into an ``OPAQUE`` node. A multi-line plain scalar's continuation lines are skipped, as is
a block scalar (``|``, ``>``). The reader never evaluates a tag: ``!tag`` and ``&anchor``
prefixes are read past. Tests: ``tests/test_pack_collector.py``.
"""
from __future__ import annotations

from typing import Union

UNPARSABLE = "unparsable_file"
UNSUPPORTED = "unsupported_yaml"
LEAF = "leaf"
OPAQUE = "opaque"

Node = Union[str, "dict[str, Node]"]


class YamlError(ValueError):
    """A file the reader cannot answer for; ``code`` is the reason code the check reports."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _quote_opens(line: str, at: int) -> bool:
    """A quote at ``at`` starts a quoted scalar: first on the line, or after ``:``/``-``/``,``/``[``/``{``."""
    before = line[:at].rstrip(" ")
    return not before or before[-1] in ":-,[{?"


def _strip_comment(line: str) -> str:
    """``line`` without its ``#`` comment; a ``#`` inside a quoted scalar is kept."""
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                if quote == "'" and line[i + 1:i + 2] == "'":
                    i += 2
                    continue
                quote = ""
        elif ch in "'\"" and _quote_opens(line, i):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _quoted_end(text: str) -> int:
    """The index just past the quoted scalar ``text`` opens with, or -1 when it does not close."""
    quote = text[0]
    i = 1
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            if quote == "'" and text[i + 1:i + 2] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return -1


def _split_key(text: str) -> tuple[str, str]:
    """``(key, value text)`` of a mapping line, or ``YamlError``."""
    if text[0] in "?":
        raise YamlError(UNSUPPORTED)
    if text[0] in "{[&*!|>%@`":
        raise YamlError(UNSUPPORTED if text[0] in "{[&*!" else UNPARSABLE)
    if text[0] in "'\"":
        end = _quoted_end(text)
        if end < 0:
            raise YamlError(UNPARSABLE)
        key, rest = text[1:end - 1], text[end:].lstrip(" ")
        if not rest.startswith(":") or rest[1:2] not in ("", " "):
            raise YamlError(UNPARSABLE)
        return key, rest[1:].strip()
    at = text.find(": ")
    if at < 0:
        if not text.endswith(":"):
            raise YamlError(UNPARSABLE)
        at = len(text) - 1
    key = text[:at].rstrip(" ")
    if key == "<<":
        raise YamlError(UNSUPPORTED)
    return key, text[at + 1:].strip()


def _value(value: str) -> str | None:
    """The node a value text holds: None for an empty value (a nested block follows),
    ``LEAF``, ``OPAQUE``, or ``"block"`` for a block scalar's header."""
    while value[:1] in ("&", "!"):  # an anchor or a tag before the value
        head, _, value = value.partition(" ")
        value = value.strip()
    if not value:
        return None
    if value[0] in "|>":
        return "block"
    if value[0] == "*":
        return OPAQUE
    if value[0] in "[{":
        if value.count(value[0]) != value.count("]" if value[0] == "[" else "}"):
            raise YamlError(UNSUPPORTED)
        return OPAQUE
    if value[0] in "'\"" and _quoted_end(value) < 0:
        raise YamlError(UNSUPPORTED)
    return LEAF


def _indent(raw: str) -> int:
    lead = raw[:len(raw) - len(raw.lstrip(" \t"))]
    if "\t" in lead:
        raise YamlError(UNPARSABLE)
    return len(lead)


class _Reader:
    """One pass over a document's lines, building the key tree."""

    def __init__(self, text: str) -> None:
        self.lines = [_strip_comment(raw) for raw in text.lstrip("\ufeff").splitlines()]
        self.root: dict[str, Node] = {}
        self.stack: list[tuple[int, dict[str, Node]]] = []
        self.pending: tuple[int, dict[str, Node], str] | None = None  # a key with an empty value
        self.scalar_indent = -1  # the last key with a plain value: deeper lines continue it
        self.started = False
        self.i = 0

    def skip_while(self, deeper_than: int, items_at: bool = False) -> None:
        """Skip blank lines and lines indented past ``deeper_than`` (and, with ``items_at``,
        list items at exactly that indent)."""
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() and _indent(line) <= deeper_than and not (
                    items_at and _indent(line) == deeper_than and line.lstrip(" ").startswith("-")):
                return
            self.i += 1

    def marker(self, indent: int, text: str) -> bool:
        """True for a document marker or directive line, which carries no key."""
        if indent or not (text in ("---", "...") or text.startswith(("--- ", "%"))):
            return False
        if text.startswith(("--- ", "%")) or (self.started and text == "---"):
            raise YamlError(UNSUPPORTED)  # a value on the marker, a directive, a second document
        return True

    def list_item(self, indent: int) -> bool:
        """Consume a list; True when the whole document is a list."""
        if self.pending is not None and indent >= self.pending[0]:
            owner_indent, parent, key = self.pending
            parent[key] = LEAF  # a list: a leaf no key walks into
            self.pending = None
            self.skip_while(owner_indent, items_at=True)
            return False
        if not self.stack and not self.root:
            return True
        raise YamlError(UNPARSABLE)

    def open_block(self, indent: int, text: str) -> bool:
        """Place a key line at ``indent``; False for a plain scalar's continuation line."""
        if self.pending is not None:
            owner_indent, parent, key = self.pending
            self.pending = None
            if indent > owner_indent:
                child: dict[str, Node] = {}
                parent[key] = child
                self.stack.append((indent, child))
        elif self.stack and indent > self.stack[-1][0]:
            if indent > self.scalar_indent >= 0:
                return False
            raise YamlError(UNPARSABLE)
        if not self.stack:
            if text[0] in "{[*&!" and ": " not in text and not text.endswith(":"):
                raise YamlError(UNSUPPORTED)  # a flow, alias or tagged document root
            self.stack.append((indent, self.root))
        while self.stack and indent < self.stack[-1][0]:
            self.stack.pop()
        if not self.stack or indent != self.stack[-1][0]:
            raise YamlError(UNPARSABLE)
        return True

    def key_line(self, indent: int, text: str) -> None:
        key, value = _split_key(text)
        node = _value(value)
        mapping = self.stack[-1][1]
        self.scalar_indent = -1
        mapping[key] = LEAF if node in (None, "block") else node
        if node is None:
            self.pending = (indent, mapping, key)
        elif node == "block":
            self.skip_while(indent)
        elif node == LEAF:
            self.scalar_indent = indent

    def read(self) -> Node:
        while self.i < len(self.lines):
            line = self.lines[self.i]
            self.i += 1
            if not line.strip():
                continue
            indent = _indent(line)
            text = line[indent:]
            if self.marker(indent, text):
                continue
            self.started = True
            if text == "-" or text.startswith("- "):
                if self.list_item(indent):
                    return LEAF  # a list document
                continue
            if self.open_block(indent, text):
                self.key_line(indent, text)
        return self.root if self.started else LEAF


def parse(text: str) -> Node:
    """The key tree of a YAML document (see the module doc); ``YamlError`` on anything else."""
    return _Reader(text).read()


def lookup(tree: Node, key: str) -> bool:
    """True when the dotted ``key`` names a node of ``tree``; ``YamlError`` when the walk
    would step into a node the reader did not open."""
    node = tree
    for part in key.split("."):
        if node == OPAQUE:
            raise YamlError(UNSUPPORTED)
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True
