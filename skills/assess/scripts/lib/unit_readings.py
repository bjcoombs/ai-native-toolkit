"""Per-unit readings of the scope-sensitive scans, each condition tagged by origin (#863, #800 step 2).

A monorepo unit (``lib.unit_discovery``, #862) inherits most of what the default maturity
model reads from the repository: root CI, review, agent settings and the root instruction
file are one reading for every unit. Only some scans see a different repository from each
unit. The ``units`` block lists the units; this module reads those scans once per unit and
leaves every other block to the single repository-wide run.

**The split.** A collector's ``unit_scope`` in its manifest fragment
(``checks/core/<key>.toml``) classifies it; a collector without one is repo-wide:

- ``scoped``: the scan's result depends on the scope, so it runs again for each unit with
  the unit's folder as ``scope``, and its blocks are the unit's own reading (origin
  ``unit``). The scoped collectors are the suppressions (``promissory``,
  ``promissory_markers``), the doc graph and staleness (``doc_graph``, ``doc_staleness``),
  the import structure (``structure_scan``, and ``keyhole_blocks`` for ``structure``) and
  size and complexity (``run_header`` for ``stats_summary``).
- ``override``: the repository-wide reading is inherited unless the unit holds its own
  evidence, which then replaces it (maintainer decision 2 on #800). ``coverage_gate`` runs
  again with the unit as scope and counts as the unit's own when one of its gates sits in
  a file under the unit; otherwise the unit inherits the parent build's gate.
  ``layer_grades`` reads ``instruction_file_present`` true, origin ``unit``, when a nested
  instruction file (``nested_instructions.files``) sits under the unit, at any depth; a
  file inside a unit is unit-local (#800 comment, refinement 2). Otherwise the root's
  reading is inherited. ``OVERRIDES`` holds the evidence rule of each; a test pins it to
  the manifest.
- Repo-wide (no ``unit_scope``): run once; every unit inherits the block. This includes
  ``ci_checks``: unit-local CI detection is not built yet, so a unit's CI conditions read
  ``inherited`` and never a false unit-local value.

**The unit pass** (``unit_table``). For each model block (``level_tally.DEFAULT_BLOCKS``)
whose collector is scoped or override, its producer is the core scan that publishes it as a
plain block, or else the block's headless variant (``headless_layers`` and ``headless_static``, as
``signals.HEADLESS_SCANS`` joins them: the core
builds ``structure`` and ``stats_summary`` inside spread entries; their variants read
``structure_scan`` and the scope directly). The producer's dependencies run again only when
they read ``scope``, directly or through another dependency; every other read
(``excludes``, ``working_notes``, ``repo_root``) is the repository-wide value, passed in
once (``UNIT_EXTERNAL_READS``, the registry entry's last reads, after ``OWN_READS``). A producer that does not read the scope is a misclassified
collector and raises ``ValueError``. The root unit (``.``) runs no pass: it reads the
repository-wide blocks (``REPO_BLOCKS``), the same scans over the same tree, as its own,
override blocks included (the root instruction file and the root gates are the root's own). Where member
units sit below a root unit, the root's scoped readings therefore cover the members' files
too (a known limit: no scan can leave out the folders of other units yet). Each unit's pass goes through ``scan_registry.run_scans``,
so a failing scan degrades to its unavailable marker for that unit alone.

**The block** (``unit_readings``, core-only, after ``headline``): ``unit_count`` and
``unit_count_reason`` copied from ``units`` (a null count stays null, #1030), ``unit_blocks``
(the model blocks a unit can read as its own), and per unit its ``path``, ``blocks`` (each
own reading, projected to the model's signal paths plus the ``available`` and ``reason``
along them and the ``EMPTY_DENOMINATORS`` counts, so ``lib.levels.resolve_signal`` and the
tally's reason codes read it as they read the full block) and ``conditions``: every model
condition id with its ``origin``, ``unit`` when a criterion reads a block in the unit's
``blocks``, else ``inherited``. A unit with an override block lacking its own evidence has
no entry for it. A degraded or absent ``units`` block yields ``available: false``.
Per-unit Steps and the roll-up are #864 and #865: overlay a unit's ``blocks`` on the run
context and run the tally.

Counts-only output drops the unit paths (``checks/core/unit_readings.toml``) and keeps the
numbers, the origin tokens and the condition ids. Tests: ``tests/test_unit_readings.py``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, TypedDict

from lib import check_manifest as cm
from lib.level_tally import DEFAULT_BLOCKS, default_model
from lib.levels import MaturityModel, condition_id
from lib.run_context_types import JsonDict
from lib.signal_grades import EMPTY_DENOMINATORS



class ScanSpec(Protocol):
    """The ``scan_registry.ScanSpec`` fields the unit pass reads (structural: this module
    never imports the registry, which reaches the platform clients through its scans)."""

    @property
    def key(self) -> str: ...
    @property
    def reads(self) -> tuple[str, ...]: ...
    @property
    def private(self) -> bool: ...
    @property
    def spread(self) -> tuple[str, ...]: ...

Origin = Literal["unit", "inherited"]
SECTION = "unit_readings"
# The repository-wide unit blocks, which the root unit (``.``) reads as its own instead of
# running the same scans again on the same tree (pinned to ``unit_blocks`` by a test).
REPO_BLOCKS = ("coverage_gate", "doc_staleness", "layer_grades", "promissory_markers",
               "stats_summary", "structure")
# The registry entry's own reads, before UNIT_EXTERNAL_READS.
OWN_READS = ("units", "nested_instructions", *REPO_BLOCKS)
# The repository-wide values the unit pass reads (pinned to ``unit_table`` by a test).
UNIT_EXTERNAL_READS = ("excludes", "repo_root", "working_notes")


class UnitCondition(TypedDict):
    """One model condition of a unit, with where its reading comes from."""

    id: str
    origin: Origin


class UnitReading(TypedDict):
    """One unit: its own block readings and the origin of each condition."""

    path: str
    blocks: dict[str, JsonDict]
    conditions: list[UnitCondition]


class UnitReadingsBlock(TypedDict):
    """The run context's ``unit_readings`` block (#863)."""

    available: bool
    unit_count: int | None
    unit_count_reason: str | None
    unit_blocks: list[str]
    units: list[UnitReading]


def _under(path: object, unit: str) -> bool:
    """True when the repo-relative ``path`` sits in the ``unit`` folder (``.`` holds every path)."""
    if not isinstance(path, str) or not path:
        return False
    if unit in ("", "."):
        return True
    return PurePosixPath(path).is_relative_to(PurePosixPath(unit))


def _coverage_own(unit: str, scoped: Any, _ctx: Mapping[str, Any]) -> JsonDict | None:
    """The unit's scoped coverage gate when one of its gates sits under the unit."""
    if not isinstance(scoped, Mapping) or scoped.get("available") is False:
        return None
    gates = scoped.get("gates")
    if isinstance(gates, list) and any(isinstance(g, Mapping) and _under(g.get("file"), unit)
                                       for g in gates):
        return dict(scoped)
    return None


def _instructions_own(unit: str, _scoped: Any, ctx: Mapping[str, Any]) -> JsonDict | None:
    """``instruction_file_present`` true when a nested instruction file sits under the unit."""
    nested = ctx.get("nested_instructions")
    files = nested.get("files") if isinstance(nested, Mapping) else None
    if isinstance(files, list) and any(isinstance(f, Mapping) and _under(f.get("path"), unit)
                                       for f in files):
        return {"available": True, "instruction_file_present": True}
    return None


# Override block -> its own-evidence rule: (unit path, the unit-scoped result or None,
# the repository-wide values) -> the unit's own block, or None to inherit.
OVERRIDES: dict[str, Callable[[str, Any, Mapping[str, Any]], JsonDict | None]] = {
    "coverage_gate": _coverage_own,
    "layer_grades": _instructions_own,
}
# Override blocks read from the repository-wide values alone (no scoped re-run).
EVIDENCE_ONLY = frozenset({"layer_grades"})


def unit_scopes(manifest: cm.CheckManifest) -> dict[str, str]:
    """Published block -> its collector's ``unit_scope``, for the classified collectors."""
    return {block: c["unit_scope"] for c in cm.collectors(manifest) if "unit_scope" in c
            for block in c["publishes"]}


def unit_blocks(manifest: cm.CheckManifest, blocks: Sequence[str] = DEFAULT_BLOCKS) -> list[str]:
    """The model blocks a unit can read as its own: those of a scoped or override collector."""
    scopes = unit_scopes(manifest)
    return [b for b in blocks if b in scopes]


def _producer(block: str, core: Mapping[str, ScanSpec], headless: Mapping[str, ScanSpec]) -> ScanSpec:
    spec = core.get(block)
    if spec is not None and not spec.spread and not spec.private:
        return spec
    if block in headless:
        return headless[block]
    raise ValueError(f"unit block {block!r} has no plain core scan and no headless variant")


def unit_table(manifest: cm.CheckManifest, core: Sequence[ScanSpec], headless: Sequence[ScanSpec],
               blocks: Sequence[str] = DEFAULT_BLOCKS) -> tuple[tuple[ScanSpec, ...], tuple[str, ...]]:
    """The specs one unit's pass runs, in run order, and the repository-wide names they read.

    Raises ``ValueError`` when a scoped or override producer does not read the scope.
    """
    by_key = {s.key: s for s in core}
    variants = {s.key: s for s in headless}
    taint: dict[str, bool] = {}

    def tainted(spec: ScanSpec) -> bool:
        if spec.key not in taint:
            taint[spec.key] = any(r == "scope" or (r in by_key and tainted(by_key[r]))
                                  for r in spec.reads)
        return taint[spec.key]

    picked: dict[str, ScanSpec] = {}
    external: set[str] = set()

    def visit(spec: ScanSpec) -> None:
        if spec.key in picked:
            return
        for name in spec.reads:
            if name == "scope":
                continue
            dep = by_key.get(name)
            if dep is not None and tainted(dep):
                visit(dep)
            else:
                external.add(name)
        picked[spec.key] = spec

    for block in unit_blocks(manifest, blocks):
        if block in EVIDENCE_ONLY:
            continue
        spec = _producer(block, by_key, variants)
        if not tainted(spec):
            raise ValueError(f"unit block {block!r}: its producer {spec.key!r} does not read the scope")
        visit(spec)
    # Core specs in table order, then the headless variants (each reads only core specs).
    order = {s.key: i for i, s in enumerate(core)}

    def place(spec: ScanSpec) -> tuple[int, int]:
        return (0, order[spec.key]) if by_key.get(spec.key) is spec else (1, 0)

    return tuple(sorted(picked.values(), key=place)), tuple(sorted(external))


# The core unit pass, bound by ``scan_registry`` (``bind``): this module never imports the
# registry, which reaches the platform clients through its scans. Built on first use.
UnitTable = tuple[tuple[ScanSpec, ...], tuple[str, ...]]
_BOUND: dict[str, Any] = {}


def bind(table: Callable[[], UnitTable], run: Callable[..., None]) -> None:
    """Set the unit pass ``read_units`` runs by default: a loader of ``unit_table``'s result
    and the registry's ``run_scans``."""
    _BOUND.clear()
    _BOUND["loader"], _BOUND["run"] = table, run


def bound_table() -> UnitTable:
    """The bound unit table, built once."""
    if "loader" not in _BOUND:
        raise ValueError("the unit pass is not bound: import lib.scan_registry first")
    if "table" not in _BOUND:
        _BOUND["table"] = _BOUND["loader"]()
    table: UnitTable = _BOUND["table"]
    return table


def _paths(model: MaturityModel) -> dict[str, list[str]]:
    """Block -> the dotted paths under it the model reads, with each statistic's set count."""
    out: dict[str, list[str]] = {}
    for row in model["rows"]:
        for level in row["levels"]:
            for c in level["criteria"]:
                for signal in (c["signal"], c.get("eq_signal")):
                    if not signal:
                        continue
                    for path in (signal, *(EMPTY_DENOMINATORS[signal][:1]
                                           if signal in EMPTY_DENOMINATORS else ())):
                        block, _, rest = path.partition(".")
                        if rest and rest not in out.setdefault(block, []):
                            out[block].append(rest)
    return out


def project(block: Any, paths: Sequence[str]) -> JsonDict:
    """``block`` cut to ``paths``, keeping each ``available`` and ``reason`` met on the way."""
    if not isinstance(block, Mapping):
        return {"available": False, "reason": "unavailable: no block"}
    out: JsonDict = {}
    for path in paths:
        src: Any = block
        dst = out
        parts = path.split(".")
        for i, part in enumerate(parts):
            if not isinstance(src, Mapping):
                break
            for key in ("available", "reason"):
                if key in src:
                    dst.setdefault(key, src[key])
            if part not in src:
                break
            if i == len(parts) - 1 or not isinstance(src[part], Mapping):
                dst[part] = src[part]
                break
            src = src[part]
            dst = dst.setdefault(part, {})
    if not out:
        for key in ("available", "reason"):
            if key in block:
                out[key] = block[key]
    return out


def _conditions(model: MaturityModel, own: Mapping[str, Any]) -> list[UnitCondition]:
    result: list[UnitCondition] = []
    for row in model["rows"]:
        for level in row["levels"]:
            reads = {c["signal"].split(".")[0] for c in level["criteria"]}
            reads |= {c["eq_signal"].split(".")[0] for c in level["criteria"] if "eq_signal" in c}
            result.append({"id": condition_id(row["id"], level["level"]),
                           "origin": "unit" if reads & own.keys() else "inherited"})
    return result


def split_reads(values: Sequence[Any]) -> tuple[dict[str, Any], tuple[Any, ...]]:
    """The registry entry's reads after ``units`` and ``nested_instructions``, split into the
    ``REPO_BLOCKS`` mapping and the ``UNIT_EXTERNAL_READS`` values."""
    return dict(zip(REPO_BLOCKS, values)), tuple(values[len(REPO_BLOCKS):])


def _scope(repo_root: Path, unit: str) -> Path | None:
    return None if unit in ("", ".") else (repo_root / unit).resolve()


def read_units(units: Any, nested_instructions: Any, *external: Any,
               table: UnitTable | None = None,
               run: Callable[..., None] | None = None,
               manifest: cm.CheckManifest | None = None,
               model: MaturityModel | None = None,
               repo_blocks: Mapping[str, Any] | None = None) -> UnitReadingsBlock:
    """The ``unit_readings`` block over the ``units`` block (see the module docstring).

    ``external`` are the values of ``UNIT_EXTERNAL_READS``, in order; ``repo_blocks`` the
    repository-wide ``REPO_BLOCKS``, which the root unit reads without a second pass.
    """
    specs, names = table if table is not None else bound_table()
    if run is None:
        run = _BOUND["run"]
    manifest = manifest if manifest is not None else cm.core_manifest()
    model = model if model is not None else default_model()
    own_blocks = unit_blocks(manifest, tuple(sorted(_paths(model))))
    scopes = unit_scopes(manifest)
    values = dict(zip(UNIT_EXTERNAL_READS, external))
    if set(names) - values.keys():
        raise ValueError(f"the unit pass reads {sorted(set(names) - values.keys())}, "
                         f"which the registry entry does not pass")
    if not isinstance(units, Mapping) or units.get("available") is False:
        return {"available": False, "unit_count": None, "unit_count_reason": "units_unavailable",
                "unit_blocks": own_blocks, "units": []}
    repo = {**values, "nested_instructions": nested_instructions}
    paths = _paths(model)
    readings: list[UnitReading] = []
    for unit in units.get("units") or []:
        rel = str(unit.get("path", "")) if isinstance(unit, Mapping) else ""
        if not rel:
            continue
        inputs: dict[str, Any] = {**values, "scope": _scope(Path(values["repo_root"]), rel)}
        ctx: dict[str, Any] = {}
        root = rel in ("", ".") and repo_blocks is not None
        if root:
            ctx.update(repo_blocks or {})  # the root unit's tree is the repository's
        elif specs:
            run(ctx, inputs, None, specs)
        own: dict[str, JsonDict] = {}
        for block in own_blocks:
            result = ctx.get(block, inputs.get(block))
            if root:
                # The repository-wide reading is the root's own, root instruction file and
                # gates included; a member unit's nested file is that unit's, not the root's.
                if result is None:
                    continue
            elif scopes[block] == "override":
                result = OVERRIDES[block](rel, result, repo)
                if result is None:
                    continue
            own[block] = project(result, paths.get(block, []))
        readings.append({"path": rel, "blocks": own, "conditions": _conditions(model, own)})
    return {"available": True, "unit_count": units.get("unit_count"),
            "unit_count_reason": units.get("unit_count_reason"),
            "unit_blocks": own_blocks, "units": readings}
