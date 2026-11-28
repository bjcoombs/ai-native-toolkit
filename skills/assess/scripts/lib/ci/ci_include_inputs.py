"""``$[[ inputs.<name> ]]`` in a GitLab include's ref, resolved offline (issue #1339).

A component often includes its own nested components at ``@$[[ inputs.version ]]``. GitLab
sets the value from the include item that read the file (its ``inputs:``, parsed into
``IncludeItem.inputs`` by ``lib.ci.ci_include_items``), else from the file's own ``spec:``
header (``spec: inputs: <name>: default:``, the lines before its ``---``). ``ci_includes``
substitutes the value before it reads the clone at the ref; a ref with an input no value is
known for reads ``input_ref``, never ``ref_missing``, so a reader can tell the two apart.

- ``spec_defaults(text)``: each input's ``default:``, None for one that is no one-line scalar;
  an input with no ``default:`` is left out. No ``---`` separator: no spec header, none.
- ``item_inputs(pairs, values)``: an include item's ``inputs:`` values, each interpolated with
  the values of the file it sits in, so a value travels down a nested chain; None where unknown.
- ``file_inputs(text, passed)``: the values a file reads, ``passed`` over its spec defaults.
- ``interpolate(ref, values)``: ``ref`` with every input substituted, or None when one has no
  known value or the interpolation is not a bare ``inputs.<name>`` (a function such as
  ``| expand_vars``, or another context).

Read line by line (stdlib); deterministic; no network. Inward-only imports:
``lib.ci.ci_include_items``, ``lib.ci.shared_tooling``. Tests: ``tests/test_ci_include_inputs.py``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from lib.ci.ci_include_items import _split_flow, _unquote
from lib.ci.shared_tooling import strip_comment

Inputs = Mapping[str, str | None]

_REF = re.compile(r"\$\[\[\s*inputs\.([A-Za-z0-9_-]+)\s*\]\]")
_PAIR = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[\w.-]+)\s*:(?:\s+(?P<value>.*))?$""")
_Rows = list[tuple[int, str]]  # (indent, content) of the non-blank lines


def interpolate(ref: str, values: Inputs) -> str | None:
    """``ref`` with each ``$[[ inputs.<name> ]]`` replaced, or None when one is not known."""
    if "$[[" not in ref:
        return ref
    unknown = False

    def value(match: re.Match[str]) -> str:
        nonlocal unknown
        found = values.get(match.group(1))
        unknown = unknown or found is None
        return found or ""

    out = _REF.sub(value, ref)
    return None if unknown or "$[[" in out else out


def scalar(raw: str) -> str | None:
    """A one-line YAML scalar's value, or None for a list, map, block scalar, anchor or tag."""
    raw = raw.strip()
    return None if not raw or raw[0] in "[{|>&*!" else _unquote(raw)


def item_inputs(pairs: Iterable[tuple[str, str]], values: Inputs) -> dict[str, str | None]:
    """An include item's ``inputs:``, interpolated with the including file's ``values``."""
    out: dict[str, str | None] = {}
    for name, raw in pairs:
        found = scalar(raw)
        out[name] = interpolate(found, values) if found is not None else None
    return out


def file_inputs(text: str, passed: Inputs) -> dict[str, str | None]:
    """The input values ``text`` reads: those its includer passed, then its spec defaults."""
    return {**spec_defaults(text), **passed}


def _mapping(rows: _Rows) -> dict[str, tuple[str, _Rows]]:
    """The keys at the first row's indent: each one's inline value and the rows under it."""
    out: dict[str, tuple[str, _Rows]] = {}
    key: str | None = None
    for indent, content in rows:
        if indent > rows[0][0] and key is not None:
            out[key][1].append((indent, content))
            continue
        match = _PAIR.match(content) if indent == rows[0][0] else None
        key = _unquote(match.group("key")) if match else None
        if key is not None and match is not None:
            out[key] = ((match.group("value") or "").strip(), [])
    return out


def _entry(value: str, rows: _Rows) -> dict[str, tuple[str, _Rows]]:
    """A key's value as a mapping: its block rows, or an inline flow map."""
    if rows:
        return _mapping(rows)
    if not value.startswith("{"):
        return {}
    pairs = (_PAIR.match(p.strip()) for p in _split_flow(value))
    return {_unquote(m.group("key")): ((m.group("value") or "").strip(), []) for m in pairs if m}


def spec_defaults(text: str) -> dict[str, str | None]:
    """Each input's ``default:`` from the ``spec:`` header of a component's file."""
    lines = [strip_comment(line).rstrip() for line in text.splitlines()]
    cut = next((i for i, line in enumerate(lines) if line == "---"), None)
    if cut is None:
        return {}
    rows = [(len(line) - len(line.lstrip(" ")), line.strip()) for line in lines[:cut] if line.strip()]
    if not rows:
        return {}
    spec = _mapping(rows).get("spec")
    inputs = _entry(*_entry(*spec).get("inputs", ("", []))) if spec is not None else {}
    out: dict[str, str | None] = {}
    for name, (value, below) in inputs.items():
        default = _entry(value, below).get("default")
        if default is not None:
            out[name] = scalar(default[0]) if not default[1] else None
    return out
