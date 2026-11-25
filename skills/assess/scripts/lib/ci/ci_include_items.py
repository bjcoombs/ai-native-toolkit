"""The ``include:`` items of a GitLab CI file, parsed without a YAML parser (issues #766, #1180).

Split out of ``lib.ci.ci_includes``, which re-exports ``IncludeItem``, ``IncludeKind``, ``KINDS`` and
``include_items``. ``include_items(text)`` parses the top-level ``include:`` of a GitLab CI file
into ``IncludeItem(kind, source, files, ref, line, conditional)`` (block list, single mapping,
flow list or mapping, bare strings; ``file:`` as a scalar, flow list or block list) and says
whether a section exists and how many entries named no item this reader parses.
``conditional`` is true when the item has a ``rules:`` key (#1180): GitLab may leave it out, so
the merged configuration (``ci_includes.merged_config``) never settles what it defines.
Inward-only imports: ``lib.ci.shared_tooling``. Tests: ``tests/test_ci_includes.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from lib.ci.shared_tooling import include_lines

# ``unknown``: an ``include:`` entry naming no item this reader parses (status ``unparsed``);
# its kind is not known, so it never counts as a local file.
IncludeKind = Literal["local", "project", "component", "template", "remote", "unknown"]
KINDS: tuple[IncludeKind, ...] = ("local", "project", "component", "template", "remote", "unknown")


@dataclass(frozen=True)
class IncludeItem:
    """One parsed ``include:`` item."""

    kind: IncludeKind
    source: str  # project or component path, local path, template name or URL
    files: tuple[str, ...]  # project files; empty for the other kinds
    ref: str | None
    line: int
    conditional: bool = False  # the item has rules: (#1180): GitLab may leave it out


_KEY = re.compile(r"""^["']?(project|component|local|template|remote|file|ref|rules)["']?\s*:(.*)$""")


# --- parsing -----------------------------------------------------------------


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _split_flow(text: str) -> list[str]:
    """``[a, {b: c, d: [e]}]`` or ``{a: b, c: [d]}`` -> its top-level items, outer brackets dropped."""
    text = text.strip()
    if text[:1] in "[{" and text[-1:] in "]}":
        text = text[1:-1]
    items: list[str] = []
    depth = 0
    quote = ""
    current = ""
    for ch in text:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        items.append(current.strip())
    return items


def _values(raw: str) -> list[str]:
    """A scalar or a flow list as a list of plain strings."""
    raw = raw.strip()
    if raw.startswith("["):
        return [v for v in (_unquote(x) for x in _split_flow(raw)) if v]
    value = _unquote(raw)
    return [value] if value else []


_ITEM_KINDS: tuple[IncludeKind, ...] = ("project", "component", "local", "template", "remote")


def _item(keys: dict[str, list[str]], line: int) -> IncludeItem | None:
    for kind in _ITEM_KINDS:
        if keys.get(kind):
            refs = keys.get("ref") or []
            return IncludeItem(kind, keys[kind][0], tuple(keys.get("file") or ()),
                               refs[0] if refs else None, line, "rules" in keys)
    return None


def _scalar_item(value: str, line: int) -> IncludeItem | None:
    raw, value = value.strip(), _unquote(value)
    if not value or (raw == value and re.match(r"^[\w.-]+\s*:(?:\s|$)", value)):
        return None  # empty, or an unquoted mapping with a key this reader does not know
    if raw == value and value[0] in "!&*":
        return None  # a YAML tag, anchor or alias (``!reference [x]``, ``*shared``): not a path
    kind: IncludeKind = "remote" if re.match(r"^[a-z]+://", value, re.IGNORECASE) else "local"
    return IncludeItem(kind, value, (), None, line)


def _flow_item(text: str, line: int) -> IncludeItem | None:
    text = text.strip()
    if not text.startswith("{"):
        return _scalar_item(text, line)
    keys: dict[str, list[str]] = {}
    for pair in _split_flow(text):
        match = _KEY.match(pair)
        if match:
            keys[match.group(1)] = _values(match.group(2))
    return _item(keys, line)


def include_items(text: str) -> tuple[list[IncludeItem], bool, int]:
    """The top-level ``include:`` items of a GitLab CI file, whether it has an ``include:``
    section at all, and how many entries named no item this reader parses (an empty
    ``project:``, an unknown key), so neither a section nor an entry it cannot read is lost."""
    rows = include_lines(text)
    if not rows:
        return [], False, 0
    first, head, _inline = rows[0]
    if not head:
        reader = _BlockReader()
        items = reader.items(rows[1:])
        return items, True, reader.dropped + (not items and not reader.dropped)
    entries = _split_flow(head) if head.startswith("[") else [head]
    items = [i for v in entries if (i := _flow_item(v, first)) is not None]
    return items, True, max(len(entries) - len(items), 0) + (not entries)


class _BlockReader:
    """Block-style include items: a list of mappings, scalars or flow items, or one mapping."""

    def __init__(self) -> None:
        self.out: list[IncludeItem] = []
        self.keys: dict[str, list[str]] = {}
        self.line = 0
        self.list_col: int | None = None
        self.key_col: int | None = None
        self.pending_file = False
        self.dropped = 0  # entries that named no parsable item
        self.started = False  # an entry is open (a "- " item, or the single mapping)

    def flush(self) -> None:
        if (done := _item(self.keys, self.line)) is not None:
            self.out.append(done)
        elif self.started:
            self.dropped += 1
        self.keys, self.pending_file, self.started = {}, False, False

    def start_item(self, content: str, col: int, number: int) -> tuple[str, int] | None:
        """A new ``- `` item: its first key's text and column, or None when the item is
        a scalar or flow item read whole."""
        self.flush()
        self.line = number
        rest = content[1:].lstrip()
        self.key_col = col + len(content) - len(rest)
        if _KEY.match(rest):
            self.started = True
            return rest, self.key_col
        whole = _flow_item(rest, number) if rest.startswith("{") else _scalar_item(rest, number)
        if whole is not None:
            self.out.append(whole)
        else:
            self.dropped += 1
        self.key_col = None
        return None

    def items(self, rows: list[tuple[int, str, bool]]) -> list[IncludeItem]:
        for number, line, _ in rows:
            content = line.lstrip()
            col = len(line) - len(content)
            if self.list_col is None and self.key_col is None and content.startswith("-"):
                self.list_col = col
            if content.startswith("-") and col == self.list_col:
                started = self.start_item(content, col, number)
                if started is None:
                    continue
                content, col = started
            elif self.key_col is None and self.list_col is None:
                self.key_col, self.line = col, number  # include: as a single mapping
                self.started = True
            self.key(content, col)
        self.flush()
        return self.out

    def key(self, content: str, col: int) -> None:
        # ">=": an indentless sequence (`- a.yml` at the key column) is valid YAML.
        if self.pending_file and self.key_col is not None and col >= self.key_col \
                and content.startswith("-"):
            self.keys.setdefault("file", []).extend(_values(content[1:]))
            return
        if self.key_col is not None and col <= self.key_col and not content.startswith("-"):
            self.pending_file = False  # any mapping key (rules:, inputs:) ends the file list
        match = _KEY.match(content)
        if match and col == self.key_col:
            name, value = match.group(1), match.group(2).strip()
            self.pending_file = name == "file" and not value
            self.keys[name] = _values(value) if value else []
