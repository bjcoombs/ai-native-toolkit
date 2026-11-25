"""Read page-written Step targets back into the targets file (issue #948, parent #705).

The fleet artifact's page lets a Contributor set a unit's Step target; it writes one
document per target into the database's ``targets`` collection (``interact`` write, #947).
``fleet.py`` stays free of network calls, so the ``/fleet`` session exports that collection
to a JSON file and ``skills/fleet/scripts/fleet_targets_import.py`` turns it into the
targets TOML that ``fleet.py --targets`` reads (``lib/fleet/fleet_targets.py``).

**Row contract** (the page writes to it; ``skills/fleet/references/publish.md``). The export
is a JSON list; each row is an object whose ``doc_id`` is ``{view}~{doc_id(unitKey)}``
(``lib.fleet.fleet_publish.doc_id``; ``unitKey`` is the unit's ``fleet-data.json`` key, or the
repository's key for the ``repo`` view) and whose ``data`` is ``{"step": <int>}`` with an
optional ``note`` string of at most ``NOTE_MAX`` characters. Every other key of a row (the
tool's envelope: version, timestamps) is never read.

**Untrusted input.** Rows are written by viewers. Each row is accepted or refused with one
reason token, checked in this order:

- ``malformed_row``: not an object, no string ``doc_id``, or ``data`` not an object;
- ``bad_doc_id``: no ``~`` separator, or a key that is not ``doc_id``'s canonical encoding;
- ``unknown_view``: the view is not ``ownership``, ``system`` or ``repo``;
- ``unknown_field``: ``data`` has a key other than ``step`` and ``note``;
- ``string_too_long``: ``note`` is not a string of at most ``NOTE_MAX`` characters;
- ``step_out_of_range``: ``step`` is not a whole number (``true`` and ``3.0`` are not) from 2 to
  the maturity model's top Step;
- ``hashed_key``: the key is a hash (a ``--hash-paths`` run): ``fleet.py --targets`` checks a
  target against the run's plain unit ids, so a hashed key cannot be read back;
- ``unknown_unit``: the key is not a unit of that view (or a repository) in the given
  ``fleet-data.json``, the bundle of the run the page shows;
- ``duplicate``: a second row for one view and unit (all of them are refused).

**Output.** Only an enumerated view, a unit id taken from the matched ``fleet-data.json``
entry (never from the row) and an integer Step reach the file, so no free text in a row is
ever copied. Targets are sorted by view and unit, written as UTF-8 with LF line endings, and
the same export and bundle give byte-identical output. An empty export still writes the file
(with no targets), so a cleared target does not linger; an export with rows of which none is
accepted (most often a bundle from another run, or a ``--hash-paths`` one) is a usage error
in the CLI, which leaves the existing file untouched. ``dumps_targets`` re-reads its text
through ``fleet_targets.parse_targets`` before returning it.

**Id length.** A target id is ``{view}~`` plus ``doc_id(unitKey)``, and a document id takes at
most 200 characters, so a unit key that encodes to more than ``200 - len(view) - 1``
characters cannot hold a target (``target_doc_id`` raises ``PublishError``); the page
offers no target control for it.

Refusals are reported by row index and reason token only: the doc id and the row's text are
viewer-written and never echoed.
"""
from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from lib.counts_only.content_free import is_hashed
from lib.fleet.fleet_achievements import REPO_VIEW
from lib.fleet.fleet_data import FleetData
from lib.fleet.fleet_publish import MAX_ID, PublishError, decode_doc_id, doc_id
from lib.fleet.fleet_targets import TARGET_VIEWS, TargetSpec, parse_targets
from lib.scoring.levels import MaturityModel

NOTE_MAX = 280
_DATA_KEYS = frozenset({"step", "note"})
SEPARATOR = "~"

MALFORMED_ROW = "malformed_row"
BAD_DOC_ID = "bad_doc_id"
UNKNOWN_VIEW = "unknown_view"
UNKNOWN_FIELD = "unknown_field"
STRING_TOO_LONG = "string_too_long"
STEP_OUT_OF_RANGE = "step_out_of_range"
HASHED_KEY = "hashed_key"
UNKNOWN_UNIT = "unknown_unit"
DUPLICATE = "duplicate"


class TargetsImportError(ValueError):
    """The export, bundle or model is unusable as a whole (a usage error, not a refused row)."""


class Refusal(NamedTuple):
    row: int  # 1-based position in the export
    reason: str  # a reason token above


class ImportResult(NamedTuple):
    targets: list[TargetSpec]
    refusals: list[Refusal]


def target_doc_id(view: str, key: str) -> str:
    """The ``targets`` document id the page writes for ``key`` (a ``fleet-data.json`` key) in ``view``;
    raises ``PublishError`` when it would exceed the database's 200-character id."""
    out = f"{view}{SEPARATOR}{doc_id(key)}"
    if len(out) > MAX_ID:
        raise PublishError(f"a target id for this {view} key is {len(out)} characters; a document id takes "
                           f"at most {MAX_ID}")
    return out


def known_units(bundle: FleetData) -> dict[tuple[str, str], str]:
    """``(view, fleet-data.json key)`` to the unit as the targets file writes it (never a blank id)."""
    known: dict[tuple[str, str], str] = {}
    for view in bundle["views"]:
        prefix = f"{view['view']}/"
        for unit in view["units"]:
            key = unit["key"]
            if not isinstance(key, str):
                raise TargetsImportError("a fleet-data.json unit key is not a string")
            if key.startswith(prefix) and key[len(prefix):].strip():
                known[(view["view"], key)] = key[len(prefix):]
    for repo in bundle["repos"]:
        if not isinstance(repo["key"], str):
            raise TargetsImportError("a fleet-data.json repository key is not a string")
        if repo["key"].strip():
            known[(REPO_VIEW, repo["key"])] = repo["key"]
    return known


def top_step(model: MaturityModel) -> int:
    return max((s["step"] for s in model.get("steps", [])), default=0)


def _row(row: object, known: Mapping[tuple[str, str], str], top: int) -> TargetSpec | str:
    """The row's target, or the reason it is refused (see the module docstring for the order)."""
    if not isinstance(row, dict):
        return MALFORMED_ROW
    ident, data = row.get("doc_id"), row.get("data")
    if not isinstance(ident, str) or not isinstance(data, dict):
        return MALFORMED_ROW
    view, sep, encoded = ident.partition(SEPARATOR)
    if not sep or not encoded:
        return BAD_DOC_ID
    try:
        key = decode_doc_id(encoded)
        canonical = doc_id(key) == encoded
    except ValueError:  # UnicodeDecodeError, or a key doc_id refuses
        return BAD_DOC_ID
    if not canonical:
        return BAD_DOC_ID
    if view not in TARGET_VIEWS:
        return UNKNOWN_VIEW
    if set(data) - _DATA_KEYS:
        return UNKNOWN_FIELD
    note = data.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > NOTE_MAX):
        return STRING_TOO_LONG
    step = data.get("step")
    if type(step) is not int or not 2 <= step <= top:
        return STEP_OUT_OF_RANGE
    if is_hashed(key):
        return HASHED_KEY
    unit = known.get((view, key))
    if unit is None:
        return UNKNOWN_UNIT
    return TargetSpec(view, unit, step)


def import_rows(rows: object, bundle: FleetData, model: MaturityModel) -> ImportResult:
    """Each exported row accepted as a target or refused with a reason token."""
    if not isinstance(rows, list):
        raise TargetsImportError("the export must be a JSON list of targets rows")
    top = top_step(model)
    if top < 2:
        raise TargetsImportError("a Step target needs a maturity model with [[steps]] above Step 1 (--levels)")
    known = known_units(bundle)
    accepted: dict[tuple[str, str], list[tuple[int, TargetSpec]]] = {}
    refusals: list[Refusal] = []
    for i, row in enumerate(rows, 1):
        result = _row(row, known, top)
        if isinstance(result, str):
            refusals.append(Refusal(i, result))
        else:
            accepted.setdefault((result.view, result.unit), []).append((i, result))
    targets: list[TargetSpec] = []
    for entries in accepted.values():
        if len(entries) == 1:
            targets.append(entries[0][1])
        else:
            refusals += [Refusal(i, DUPLICATE) for i, _ in entries]
    targets.sort(key=lambda t: (t.view, t.unit))
    refusals.sort()
    return ImportResult(targets, refusals)


def _quoted(value: str) -> str:
    """A TOML basic string (JSON escapes are valid TOML escapes for these characters)."""
    out = []
    for ch in value:
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


HEADER = ("# Step targets read back from the fleet artifact's targets collection (fleet_targets_import.py).\n"
          "# Regenerated on each import; edit the targets in the page.\n")


def dumps_targets(targets: Sequence[TargetSpec], model: MaturityModel) -> str:
    """The targets file for ``fleet.py --targets``: sorted, byte-stable, checked by ``parse_targets``."""
    blocks = [f"\n[[target]]\nview = {_quoted(t.view)}\nunit = {_quoted(t.unit)}\nstep = {t.step}\n"
              for t in sorted(targets, key=lambda t: (t.view, t.unit))]
    text = HEADER + "".join(blocks)
    if parse_targets(tomllib.loads(text), model) != sorted(targets, key=lambda t: (t.view, t.unit)):
        raise AssertionError("the written targets do not read back as written")
    return text
