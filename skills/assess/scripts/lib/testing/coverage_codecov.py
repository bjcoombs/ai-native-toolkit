"""Read Codecov status targets from a committed ``codecov.yml`` (issue #882), read-only.

Codecov posts a ``codecov/project`` (or ``codecov/patch``) commit status that fails
when coverage misses the status ``target``. ``coverage_gate`` reports each status as a
gate with tool ``codecov``, ``metric`` ``project`` or ``patch``, ``unit`` ``percent``
and the target as written (``80%`` and ``80`` are 80); ``target: auto``, a status with
no ``target`` (Codecov's default is ``auto``: the base commit's coverage) and a target
that is not a number read ``threshold: null``.

How a status counts toward Layer 6 level 1 (maintainer-approved, #882):

- A project status counts like any other gate: ``target: 80%`` makes
  ``threshold_meets_minimum`` true, and an ``auto`` target makes it null with
  ``unmeasured_reason`` ``codecov_target_not_read`` (``CODECOV_UNMEASURED``) when no
  other gate decides it, never false.
- A patch status bounds only the changed lines (removing a test changes none), so
  ``threshold_meets_minimum`` leaves it out (``is_patch``) and the other gates decide:
  a project target of 20 beside a patch target of 100 reads false, and so does a patch
  status beside a CI ``--cov-fail-under=40``. When patch statuses are the only gates,
  the reading is null with ``codecov_patch_only`` (``PATCH_ONLY``), never false, and
  ``max_threshold_percent`` leaves them out too (``meets_percent``).
- The status is server-side: it gates a merge only when branch protection requires it,
  which this read does not see. So a Codecov gate never reads as run by CI:
  ``lib.testing.coverage_ci`` reads it null with ``enforced_basis`` ``status_check_unconfirmed``
  (``STATUS_UNCONFIRMED``); a patch status is skipped there too, so it never turns a
  known false (an unrun ``fail_under``) into null. Codecov statuses alone therefore
  never make Layer 6 level 1 true or false: a numeric project target reads
  ``status_check_unconfirmed``, an ``auto`` one ``threshold_not_read``, and patch
  statuses alone ``threshold_not_read`` (with ``codecov_patch_only``).

Not gates: ``informational: true`` (the status never fails), ``off`` / ``false`` on the
status, on ``project`` / ``patch`` or on ``coverage.status``. A YAML anchor on a status
key is dropped (``default: &base`` reads the block below it, ``default: &b {target: 80%}``
its flow map); an alias (``default: *base``) is a status whose target is not read, so it
reads unknown, never absent. A byte-order mark is skipped. A flow map holding several
statuses (``project: {a: {target: 80}, b: {target: 90}}``) reads as one, with its first
target. The first file found is read,
as Codecov does: ``codecov.yml``, ``.codecov.yml``, ``codecov.yaml``, ``.codecov.yaml``,
at the repository root, then in ``.github/``, then in ``dev/`` (on a scoped run too: the
config is the repository's). A config with no ``coverage.status`` section posts Codecov's
default statuses; it is not read as a gate. The YAML is read by indentation (block maps,
one-line flow maps, comments and quotes), stdlib only; a line it cannot read is skipped,
never raised. Tests: ``tests/test_coverage_codecov.py``.

Inward-only imports: stdlib and ``lib.assess_config.is_user_excluded``; never
``lib.testing.coverage_gate`` (which calls it) or an orchestrator.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from lib.assess_config import is_user_excluded

CODECOV_UNMEASURED = "codecov_target_not_read"
PATCH_ONLY = "codecov_patch_only"
STATUS_UNCONFIRMED = "status_check_unconfirmed"
TOOL = "codecov"

_NAMES = ("codecov.yml", ".codecov.yml", "codecov.yaml", ".codecov.yaml")
_DIRS = ("", ".github", "dev")
_KINDS = ("project", "patch")
_MAX_BYTES = 1_000_000
_OFF = {"off", "false", "no"}
_ON = {"on", "true", "yes"}
_KEY_RE = re.compile(r"^( *)(\"[^\"]*\"|'[^']*'|[^\s:#\"'{}\[\]-][^:#]*?)\s*:(?:\s+(.*))?$")
_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")
_NUMBER_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*%?$")
_FLOW_TARGET_RE = re.compile(r"[{,\s]target\s*:\s*([^,}]+)")
_ANCHOR_RE = re.compile(r"^&\S+\s*")
_FLOW_INFO_RE = re.compile(r"[{,\s]informational\s*:\s*([^,}\s]+)")

Excludes = tuple[set[str], list[str]]
G = TypeVar("G", bound=Mapping[str, object])


@dataclass(frozen=True)
class Status:
    """One Codecov status: its file, the 1-based line of its target (or of its key when
    it has none), ``project`` or ``patch``, its name and its target as a percentage."""

    file: str
    line: int
    kind: str
    name: str
    target: float | None

    @property
    def form(self) -> str:
        return f"coverage.status.{self.kind}.{self.name}.target"


def _scalar(text: str) -> str:
    return text.strip().strip("\"'").strip().lower()


def target_percent(text: str | None) -> float | None:
    """A ``target`` value as a percentage: ``80%``, ``"80"`` and ``80.5`` read; ``auto``,
    a missing target and anything else read None (not read)."""
    match = _NUMBER_RE.match(_scalar(text)) if text is not None else None
    return float(match.group(1)) if match else None


def _entries(lines: Sequence[str]) -> list[tuple[int, tuple[str, ...], str]]:
    """``(line, key path, value)`` for each block-map key, by indentation."""
    out: list[tuple[int, tuple[str, ...], str]] = []
    stack: list[tuple[int, str]] = []
    for idx, raw in enumerate(lines, 1):
        match = _KEY_RE.match(_COMMENT_RE.sub("", raw.expandtabs(2)).rstrip())
        if match is None:
            continue
        value = _ANCHOR_RE.sub("", (match.group(3) or "").strip())
        indent = len(match.group(1))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, match.group(2).strip("\"'")))
        out.append((idx, tuple(key for _, key in stack), value))
    return out


def _flow(file: str, line: int, kind: str, name: str, value: str) -> list[Status]:
    """A one-line flow map (``{target: 80%}``, or ``{default: {target: 80%}}``)."""
    info = _FLOW_INFO_RE.search(value)
    if info and _scalar(info.group(1)) in _ON:
        return []
    target = _FLOW_TARGET_RE.search(value)
    return [Status(file, line, kind, name, target_percent(target.group(1) if target else None))]


def _statuses(file: str, lines: Sequence[str]) -> list[Status]:
    entries = _entries(lines)
    found: list[Status] = []
    for pos, (line, path, value) in enumerate(entries):
        if path[:2] != ("coverage", "status") or len(path) not in (3, 4) or path[2] not in _KINDS:
            continue
        kind, name = path[2], path[3] if len(path) == 4 else "default"
        word = _scalar(value)
        if word in _OFF or (len(path) == 3 and not value):
            continue
        if value.startswith("{"):
            found += _flow(file, line, kind, name, value)
        elif value.startswith("*"):   # an alias: the status it names is not resolved
            found.append(Status(file, line, kind, name, None))
        elif word in _ON or (len(path) == 4 and not value):
            found += _block(file, line, kind, name, entries[pos + 1:], path)
    if any(path == ("coverage", "status") and _scalar(v) in _OFF for _, path, v in entries):
        return []
    return found


def _block(file: str, line: int, kind: str, name: str,
           rest: Sequence[tuple[int, tuple[str, ...], str]], path: tuple[str, ...]) -> list[Status]:
    """A status written as a block map: its ``target`` and ``informational`` children."""
    target: tuple[int, str] | None = None
    for row, child, value in rest:
        if child[:len(path)] != path or len(child) == len(path):
            break
        if child == (*path, "informational") and _scalar(value) in _ON:
            return []
        if child == (*path, "target"):
            target = (row, value)
    if target is None:
        return [Status(file, line, kind, name, None)]
    return [Status(file, target[0], kind, name, target_percent(target[1]))]


def config_path(root: Path, excludes: Excludes = (set(), [])) -> str | None:
    """The repo-relative Codecov config Codecov would read, or None."""
    for folder in _DIRS:
        for name in _NAMES:
            rel = Path(folder) / name
            if (root / rel).is_file() and not is_user_excluded(rel, *excludes):
                return rel.as_posix()
    return None


def codecov_statuses(root: Path, excludes: Excludes = (set(), [])) -> list[Status]:
    """The status targets in the repository's Codecov config; [] with none, or when it
    cannot be read (over 1 MB, unreadable)."""
    rel = config_path(root, excludes)
    if rel is None:
        return []
    try:
        if (root / rel).stat().st_size > _MAX_BYTES:
            return []
        text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, ValueError):
        return []
    return _statuses(rel, text.splitlines())


def is_patch(gate: Mapping[str, object]) -> bool:
    """A Codecov patch status: it bounds changed lines, never total coverage."""
    return gate["tool"] == TOOL and gate["metric"] == "patch"


def meets_percent(gate: Mapping[str, object]) -> object:
    """The percentage of total coverage a gate holds: its ``threshold_percent``, or None
    for a Codecov patch status, which bounds changed lines only."""
    return None if is_patch(gate) else gate["threshold_percent"]


def codecov_reason(gates: Sequence[G], counted: Callable[[G], bool]) -> str | None:
    """The ``unmeasured_reason`` for a null ``threshold_meets_minimum`` when every counted
    gate whose percentage is not known is a Codecov status: ``codecov_target_not_read``
    when one is a project status, ``codecov_patch_only`` when all are patch; else None."""
    unread = [g for g in gates if counted(g) and meets_percent(g) is None]
    if not unread or not all(g["tool"] == TOOL for g in unread):
        return None
    return PATCH_ONLY if all(is_patch(g) for g in unread) else CODECOV_UNMEASURED
