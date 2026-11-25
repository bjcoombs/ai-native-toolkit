"""Load the definition of ready from its document: the one fenced `yaml` block (issue #1320).

The refinement script holds no copy of the ready checklist, the section names
or the label names. It reads them from `docs/contributing/definition-of-ready.md`
(relative to the repository root, #1319), section "Machine-readable
definition", which holds exactly one fenced `yaml` block.

The script is stdlib only, so `parse_yaml_subset` reads the YAML subset that
block uses and refuses anything else with `DefinitionError`:

- block mappings (`key: value`, `key:` followed by an indented block);
- block sequences (`- item`), including sequences of mappings
  (`- id: x` with further keys indented under it);
- flow sequences of scalars (`[2, 3]`);
- scalars: integers, single- or double-quoted strings, plain strings;
- full-line `#` comments and blank lines.

`load_definition` then checks every key the script reads is present with the
right type, so a renamed key fails loudly instead of disabling a check.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from refine_types import ChecklistEntry, Definition, Labels

DEFAULT_PATH = Path("docs") / "contributing" / "definition-of-ready.md"

_FENCE_OPEN = re.compile(r"^```yaml\s*$")
_MAP_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$")

Line = Tuple[int, str]  # (indent, content)


class DefinitionError(ValueError):
    """The definition document or its block is missing, ambiguous or malformed."""


def extract_block(markdown: str) -> str:
    """The body of the document's only fenced `yaml` block."""
    blocks: List[str] = []
    current: List[str] = []
    inside = False
    for line in markdown.splitlines():
        if not inside and _FENCE_OPEN.match(line):
            inside, current = True, []
        elif inside and line.strip() == "```":
            inside = False
            blocks.append("\n".join(current))
        elif inside:
            current.append(line)
    if len(blocks) != 1:
        raise DefinitionError(f"expected exactly one fenced yaml block, found {len(blocks)}")
    return blocks[0]


def _lines(text: str) -> List[Line]:
    out: List[Line] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise DefinitionError(f"tab in indentation: {raw!r}")
        out.append((len(raw) - len(raw.lstrip(" ")), stripped))
    return out


def _scalar(text: str) -> Any:
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text[:1] in "{&*!|>%@`" or text.startswith("- "):
        raise DefinitionError(f"unsupported YAML scalar: {text!r}")
    return text


def _parse_block(lines: List[Line], i: int, indent: int) -> Tuple[Any, int]:
    if lines[i][1] == "-" or lines[i][1].startswith("- "):
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_value(lines: List[Line], i: int, indent: int, rest: str) -> Tuple[Any, int]:
    """The value of a key on line i-1, whose inline text is `rest`."""
    if rest:
        return _scalar(rest), i
    if i < len(lines) and lines[i][0] > indent:
        return _parse_block(lines, i, lines[i][0])
    if i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
        return _parse_seq(lines, i, indent)
    return None, i


def _parse_map(lines: List[Line], i: int, indent: int) -> Tuple[Any, int]:
    result: Dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
        match = _MAP_KEY.match(lines[i][1])
        if not match:
            raise DefinitionError(f"expected 'key: value', got {lines[i][1]!r}")
        key, rest = match.group(1), (match.group(2) or "").strip()
        result[key], i = _parse_value(lines, i + 1, indent, rest)
    if i < len(lines) and lines[i][0] > indent:
        raise DefinitionError(f"unexpected indentation at {lines[i][1]!r}")
    return result, i


def _parse_seq(lines: List[Line], i: int, indent: int) -> Tuple[Any, int]:
    result: List[Any] = []
    while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
        item = lines[i][1][2:].strip()
        if _MAP_KEY.match(item):
            # A mapping item: its first key sits on the dash line, the rest
            # are indented to the column after "- ".
            sub = [(indent + 2, item)] + lines[i + 1:]
            value, used = _parse_map(sub, 0, indent + 2)
            result.append(value)
            i = i + used
        else:
            result.append(_scalar(item))
            i += 1
    return result, i


def parse_yaml_subset(text: str) -> Any:
    """Parse the YAML subset described in the module docstring."""
    lines = _lines(text)
    if not lines:
        raise DefinitionError("empty yaml block")
    value, end = _parse_block(lines, 0, lines[0][0])
    if end != len(lines):
        raise DefinitionError(f"unparsed YAML from {lines[end][1]!r}")
    return value


def _require(data: Any, key: str, kind: type) -> Any:
    if not isinstance(data, dict) or key not in data:
        raise DefinitionError(f"definition block has no {key!r}")
    value = data[key]
    if not isinstance(value, kind):
        raise DefinitionError(f"definition key {key!r} is not a {kind.__name__}")
    return value


def _str_list(data: Any, key: str) -> List[str]:
    values = _require(data, key, list)
    if not all(isinstance(v, str) for v in values):
        raise DefinitionError(f"definition key {key!r} must list strings")
    return list(values)


def definition_from_block(block: str) -> Definition:
    """Validate a parsed block into a `Definition`."""
    data = parse_yaml_subset(block)
    levels = _require(data, "heading_levels", list)
    if not all(isinstance(v, int) for v in levels):
        raise DefinitionError("definition key 'heading_levels' must list integers")
    labels = _require(data, "labels", dict)
    checklist: List[ChecklistEntry] = []
    for entry in _require(data, "checklist", list):
        check = _require(entry, "check", str)
        if check not in ("script", "judgement"):
            raise DefinitionError(f"checklist check must be script or judgement, got {check!r}")
        checklist.append(ChecklistEntry(id=_require(entry, "id", str), check=check))
    pattern = _require(data, "size_pattern", str)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise DefinitionError(f"size_pattern does not compile: {exc}") from exc
    return Definition(
        version=_require(data, "version", int),
        sections=_str_list(data, "sections"),
        heading_levels=list(levels),
        empty_markers=_str_list(data, "empty_markers"),
        max_points=_require(data, "max_points", int),
        size_pattern=pattern,
        labels=Labels(triage=_require(labels, "triage", str), ready=_require(labels, "ready", str)),
        checklist=checklist,
    )


def load_definition(path: Path) -> Definition:
    """Read the definition of ready from the document at `path`."""
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DefinitionError(f"cannot read the definition of ready at {path.as_posix()}: {exc}") from exc
    return definition_from_block(extract_block(markdown))
