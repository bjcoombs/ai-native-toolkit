"""Practice packs layered onto a maturity model, each row naming its source (#1274, part C of #1266).

``layer_packs(base, base_source, packs, validator)`` is the model a run scores with: the
chosen base model (the default, a ``--profile`` or a ``--levels`` file) followed by the
``[[rows]]`` of each ``--pack`` (``lib/packs.py``), in pass order. Each row of the result
carries ``source``: ``base_source`` (``default``, the profile name or the ``--levels`` file
name, ``lib.profiles.ModelChoice.name``) for a base row, the pack name for a pack row. The
order is the run context's: conditions are listed in model order and the ``packs`` block
names the packs in pass order.

When no pack holds a row, ``base`` is returned as it is, with no ``source`` on any row, so a
run without ``--pack``, or with packs of checks only, gives the same ``level`` block and the
same ``rules_sha256`` (``lib/fleet/fleet_method.py``) as before packs held rows. Once a pack
row is layered, ``source`` is a hashed row key: adding a pack row, or moving one from one pack
to another, changes ``rules_sha256``.

Load errors (``PackError``), raised before any scan:

- a pack named like the base source (``default`` or a profile name), so a row's ``source``
  always tells a pack from the base;
- a row id defined by two sources, naming both: no silent override;
- a ``requires`` naming a condition of a later pack (a pack row may require the base model or
  an earlier pack, never a later one), or a condition no source defines;
- a pack row signal path ``validator`` rejects (``lib.scoring.levels.validate_paths``).
- a pack hazard whose ``blocks`` names no gate of the base model's Steps (#828).

``pack_sources(model)`` lists the pack names a layered model holds rows from, in order;
``row_source(model, row_id)`` is one row's source, or None for an unlayered model.

Tests: ``tests/test_pack_layering.py``.
"""
from __future__ import annotations

from collections.abc import Sequence

from lib.scoring.levels import LevelsError, MaturityModel, PathValidator, Row, condition_id, validate_paths
from lib.scoring.levels_fields import check_hazard_gates
from lib.packs import Pack, PackError

__all__ = ["layer_packs", "pack_sources", "row_source"]


def _conditions(rows: Sequence[Row]) -> set[str]:
    return {condition_id(row["id"], lv["level"]) for row in rows for lv in row["levels"]}


def _check_requires(pack: Pack, defined: set[str], later: dict[str, str]) -> None:
    """Every ``requires`` of ``pack``'s rows names a condition of the base, an earlier pack or
    the pack itself; one of a later pack is refused by name."""
    for row in pack["rows"]:
        for lv in row["levels"]:
            cid = condition_id(row["id"], lv["level"])
            for need in lv.get("requires", []):
                if need in defined:
                    continue
                if need in later:
                    raise PackError(
                        f"pack {pack['name']!r}: condition {cid!r} requires {need!r} of the later pack "
                        f"{later[need]!r}; a pack row may require the base model or an earlier pack, "
                        "so pass the packs in that order")
                raise PackError(f"pack {pack['name']!r}: condition {cid!r} requires unknown condition {need!r}")


def layer_packs(base: MaturityModel, base_source: str, packs: Sequence[Pack],
                validator: PathValidator | None = None) -> MaturityModel:
    """``base`` followed by each pack's rows, every row with its ``source``; ``base`` itself
    when no pack holds a row. ``PackError`` on a clash, a forward ``requires`` or a bad path."""
    if not any(pack["rows"] for pack in packs):
        return base
    owner: dict[str, str] = {row["id"]: base_source for row in base["rows"]}
    rows: list[Row] = [{**row, "source": base_source} for row in base["rows"]]
    defined = _conditions(base["rows"])
    for index, pack in enumerate(packs):
        if pack["name"] == base_source:
            raise PackError(f"pack {pack['name']!r} has the name of the base model; give the pack its own name")
        for row in pack["rows"]:
            if row["id"] in owner:
                raise PackError(f"row {row['id']!r} is defined by both {owner[row['id']]!r} and "
                                f"pack {pack['name']!r}; rename one, a pack never overrides a row")
            owner[row["id"]] = pack["name"]
        later = {cid: p["name"] for p in packs[index + 1:] for cid in _conditions(p["rows"])}
        defined |= _conditions(pack["rows"])
        _check_requires(pack, defined, later)
        try:
            if validator is not None:
                validate_paths(pack["rows"], validator)
            check_hazard_gates(pack["rows"], base.get("steps"))  # a pack hazard's blocks names a base gate (#828)
        except LevelsError as exc:
            raise PackError(f"pack {pack['name']!r}: {exc}") from exc
        rows += [{**row, "source": pack["name"]} for row in pack["rows"]]
    layered: MaturityModel = {**base, "rows": rows}
    return layered


def row_source(model: MaturityModel, row_id: str) -> str | None:
    """The ``source`` of row ``row_id``; None when the model is not layered or has no such row."""
    return next((row.get("source") for row in model["rows"] if row["id"] == row_id), None)


def pack_sources(model: MaturityModel) -> list[str]:
    """The packs a layered model holds rows from, in order; ``[]`` for an unlayered model."""
    sources = [row["source"] for row in model["rows"] if "source" in row]
    base = sources[0] if sources else None
    return [s for s in dict.fromkeys(sources) if s != base]
