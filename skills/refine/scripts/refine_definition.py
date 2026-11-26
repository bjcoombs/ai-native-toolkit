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

`resolve_path` finds the document the way `refine.py --definition` defaults:
under the current directory, then under this toolkit checkout. `size_limit`
(after `parse_override`) gives the write scripts the `max_points` the verdict records (#1439 follow-up): an explicit
`REFINE_MAX_POINTS` above 0 overrides it, otherwise the definition decides.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from refine_types import ChecklistEntry, Definition, Labels

DEFAULT_PATH = Path("docs") / "contributing" / "definition-of-ready.md"
TOOLKIT_ROOT = Path(__file__).resolve().parents[3]

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


def _regex(data: Any, key: str) -> str:
    """A key holding a regular expression with one integer group."""
    pattern = _require(data, key, str)
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise DefinitionError(f"{key} does not compile: {exc}") from exc
    if compiled.groups < 1:
        raise DefinitionError(f"{key} needs a group that captures the points")
    return str(pattern)


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
    pattern = _regex(data, "size_pattern")
    marker = _regex(data, "size_marker")
    return Definition(
        version=_require(data, "version", int),
        sections=_str_list(data, "sections"),
        heading_levels=list(levels),
        empty_markers=_str_list(data, "empty_markers"),
        max_points=_require(data, "max_points", int),
        size_pattern=pattern,
        size_marker=marker,
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


def resolve_path(arg: Optional[str] = None) -> Path:
    """`arg`, else the definition under the current directory, else this toolkit's copy."""
    if arg:
        return Path(arg)
    # Relative when found under the current directory, so the plan names it
    # the same way on every machine.
    return DEFAULT_PATH if DEFAULT_PATH.is_file() else TOOLKIT_ROOT / DEFAULT_PATH


def parse_override(raw: str) -> int:
    """`REFINE_MAX_POINTS` as an integer: 0 when empty, `DefinitionError` unless a whole number >= 0."""
    text = raw.strip()
    if not text:
        return 0
    if not re.fullmatch(r"\d+", text):
        raise DefinitionError(f"REFINE_MAX_POINTS must be 0 (unset) or a positive integer, not {raw!r}")
    return int(text)


def size_limit(override: int, fallback: int, ref: Any = None) -> int:
    """The largest size that can be ready: the `max_points` `refine.py`'s `sized` check used.

    `override` (`parse_override` of `REFINE_MAX_POINTS`) wins when above 0;
    0 means unset. Otherwise `ref`, the verdict's `definition` block, names the
    `max_points` `refine.py` read, whichever definition file it read (the
    verdict is digest-checked between the plan and apply jobs). A verdict
    without one falls back to the definition at `resolve_path()`, and when
    neither place has that document to `fallback`, which a test keeps equal to
    this toolkit's copy.
    """
    if override:
        return override
    recorded = ref.get("max_points") if isinstance(ref, dict) else None
    if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded >= 1:
        return recorded
    path = resolve_path()
    if not path.is_file():
        return fallback
    return int(load_definition(path)["max_points"])
