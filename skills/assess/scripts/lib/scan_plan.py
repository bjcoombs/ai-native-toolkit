"""The scans a maturity model needs, worked out from the model before any scan runs (#1268).

`/assess --profile <name>` and `--levels FILE` used to run every registered scan and then
score the run context with the chosen model, so a light profile cost as much as a full run.
A model's criteria name signal paths (`secrets_guard.enforcement.present`), and the check
catalogue says which collector publishes each block (`check_manifest.block_collectors`), so
the scans a model reads are known up front.

`plan_scans(blocks, table)` is the set of registry keys a run needs: the frame
(`frame_keys`: every non-degrading setup entry, which is the run's own state and the publish
step's inputs, plus `FRAME_SCANS`: the headline, the level with its owner exclusions, the
offers, the decline markers and the anomalies), the collector of every block in `blocks`,
and every entry those read, transitively. `MODEL_READERS` (`level`) read whatever the model
names, so their declared reads (the default model's blocks) are not followed: the blocks
passed in stand for them; `OPTIONAL_READS` (the top card's unit roll-up) are not followed
either. `model_plan(model, label)` is that set for a model's blocks, as a
`ScanPlan`; `extra_signals` and `extra_scans` let a collector add the blocks or scans it
needs (a practice pack, #1273) without editing this module. `run_plan(choice, all_scans)` is
the plan a single-repository run uses: None (every scan) for the default model or
`--all-scans`, else `model_plan` of the chosen model.

`scan_registry.run_scans(..., plan=...)` runs only `plan.scans` and writes `plan.marker()`,
`{"available": false, "reason": "not_run: not run under profile citizen"}`, wherever a
skipped entry would have written, so every key keeps its place, every reader takes the
unavailable marker it already accepts (#623), and nothing reads as zero or unmet. The
`scan_plan` run-context key (`run_setup.scan_plan_stamp`) records the plan's `model`, the
scans it `ran` and the ones `not_run`, and is absent from a full run, whose run context is
unchanged. `/fleet --levels` and `--profile` run `fleet_scans(model)` (the headless scan
names of the model's blocks plus the owner exclusions) for every repository unless
`--scans` or `--all-scans` is given. Tests: `tests/test_scan_plan.py`.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

import lib.manifest.check_manifest as cm
from lib.level_tally import EXCLUSIONS_BLOCK, default_model, model_blocks
from lib.levels import MaturityModel

if TYPE_CHECKING:
    from lib.profiles import ModelChoice
    from lib.scan_registry import ScanSpec

# The reason code a skipped scan's marker opens with (lib/counts_only/reason_codes.py).
NOT_RUN_CODE = "not_run"

# Entries every narrowed run keeps besides the setup gates: the level and the headline
# worded from it (with what changed since the last run), the owner's exclusions the level
# reads, the offers and decline markers the orchestrator reads, the anomalies, the
# generated-file disclosure and the plugin version.
FRAME_SCANS: tuple[str, ...] = (
    "plugin_version", "decline", "owner_exclusions", "offers_block", "excluded_generated",
    "uninstall_instructions_path", "anomalies", "level", "since_last_run", "headline",
)

# Entries whose reads are the model's blocks, not a fixed list: the model decides.
MODEL_READERS: frozenset[str] = frozenset({"level"})

# Reads a plan does not follow: the top card (``headline``, #1252) reads the monorepo unit
# roll-up only for its "copy it from <unit>" hint, and the unit pass costs one scoped run per
# unit. Under a plan the roll-up is a not-run marker and the card leaves the hint out; no card
# fact reads it as zero or unmet.
OPTIONAL_READS: dict[str, frozenset[str]] = {"headline": frozenset({"unit_rollup"})}


class ScanPlanError(ValueError):
    """A model names a block no collector publishes, or a scan the table does not hold."""


class ScanPlanStamp(TypedDict):
    """The run-context ``scan_plan`` block: the model a narrowed run was planned for, the
    scans it ran and the scans it left out, each sorted."""

    model: str
    ran: list[str]
    not_run: list[str]


@dataclass(frozen=True)
class ScanPlan:
    """The registry keys a run executes; ``label`` names the model (``profile citizen``)."""

    label: str
    scans: frozenset[str]
    not_run: tuple[str, ...]

    def runs(self, key: str) -> bool:
        return key in self.scans

    def reason(self) -> str:
        """``not_run: not run under profile citizen``."""
        return f"{NOT_RUN_CODE}: not run under {self.label}"

    def marker(self) -> dict[str, Any]:
        """The value a skipped entry leaves in its place: unavailable, never zero."""
        return {"available": False, "reason": self.reason()}

    def stamp(self) -> ScanPlanStamp:
        return {"model": self.label, "ran": sorted(self.scans), "not_run": list(self.not_run)}


def _table(table: Sequence[ScanSpec] | None) -> Sequence[ScanSpec]:
    if table is not None:
        return table
    from lib.scan_registry import SCANS  # lazy: the registry imports the setup adapters

    return SCANS


def frame_keys(table: Sequence[ScanSpec]) -> frozenset[str]:
    """Every entry a narrowed run keeps whatever the model: the setup gates and ``FRAME_SCANS``."""
    keys = {spec.key for spec in table}
    setup = {spec.key for spec in table if spec.stage == "setup" and not spec.degrade}
    return frozenset(setup | (set(FRAME_SCANS) & keys))


def block_scans(table: Sequence[ScanSpec],
                manifest: cm.CheckManifest | None = None) -> dict[str, str]:
    """Run-context block -> the registry key of the entry that publishes it, from the check
    catalogue's collector map, falling back to the table for a block the catalogue omits."""
    keys = {spec.key for spec in table}
    found: dict[str, str] = {}
    for spec in table:
        for name in spec.spread or (() if spec.private else (spec.key,)):
            found[name] = spec.key
    catalogue = cm.block_collectors(cm.core_manifest() if manifest is None else manifest)
    for block, collector in catalogue.items():
        key = collector.split(".", 1)[1]
        if key in keys:
            found[block] = key
    return found


def plan_scans(blocks: Iterable[str], table: Sequence[ScanSpec] | None = None, *,
               extra_scans: Iterable[str] = (),
               manifest: cm.CheckManifest | None = None) -> frozenset[str]:
    """The frame, the publisher of each of ``blocks``, ``extra_scans`` and every entry those
    read, transitively (``MODEL_READERS`` excepted); ``ScanPlanError`` on an unknown name."""
    specs = _table(table)
    by_key = {spec.key: spec for spec in specs}
    publisher = block_scans(specs, manifest)
    blocks, extra_scans = list(blocks), list(extra_scans)
    unknown = sorted({b for b in blocks if b not in publisher}
                     | {s for s in extra_scans if s not in by_key})
    if unknown:
        raise ScanPlanError(f"no scan publishes {', '.join(unknown)}")
    todo = [*frame_keys(specs), *(publisher[b] for b in blocks), *extra_scans]
    planned: set[str] = set()
    while todo:
        key = todo.pop()
        if key in planned:
            continue
        planned.add(key)
        if key in MODEL_READERS:
            continue
        optional = OPTIONAL_READS.get(key, frozenset())
        todo.extend(name if name in by_key else publisher[name] for name in by_key[key].reads
                    if (name in by_key or name in publisher) and name not in optional)
    return frozenset(planned)


def model_plan(model: MaturityModel, label: str, table: Sequence[ScanSpec] | None = None, *,
               extra_signals: Iterable[str] = (), extra_scans: Iterable[str] = ()) -> ScanPlan:
    """The plan for ``model``: its blocks plus the owner exclusions, and the blocks of
    ``extra_signals`` (signal paths) and the ``extra_scans`` a collector adds."""
    specs = _table(table)
    blocks = {*model_blocks(model), EXCLUSIONS_BLOCK, *(s.split(".")[0] for s in extra_signals)}
    scans = plan_scans(sorted(blocks), specs, extra_scans=extra_scans)
    return ScanPlan(label, scans, tuple(sorted(s.key for s in specs if s.key not in scans)))


def is_default(model: Mapping[str, Any]) -> bool:
    """True for the default model, which always runs every scan."""
    return model.get("version") == default_model()["version"]


def run_plan(choice: ModelChoice | None, all_scans: bool = False,
             table: Sequence[ScanSpec] | None = None) -> ScanPlan | None:
    """The plan a single-repository run uses; None (every scan) for the default model or
    ``--all-scans``."""
    if all_scans or choice is None or choice.kind == "default" or is_default(choice.model):
        return None
    label = f"profile {choice.name}" if choice.kind == "profile" else f"model {choice.name}"
    return model_plan(choice.model, label, table)


def fleet_scans(model: MaturityModel | None, known: Collection[str],
                all_scans: bool = False) -> list[str] | None:
    """The headless scan names a ``/fleet --levels`` run asks every repository for: the
    model's blocks and the owner exclusions that ``known`` (the runner's names) holds, in
    sorted order; None (every scan) for no model, the default model or ``--all-scans``."""
    if all_scans or model is None or is_default(model):
        return None
    return sorted({*model_blocks(model), EXCLUSIONS_BLOCK} & set(known))


def not_run_text(block: object) -> str | None:
    """``not run under profile citizen`` when ``block`` is a skipped scan's marker, else None:
    the words a report section shows in place of a block the plan left out."""
    if not isinstance(block, Mapping) or block.get("available") is not False:
        return None
    reason = block.get("reason")
    prefix = f"{NOT_RUN_CODE}: "
    return reason[len(prefix):] if isinstance(reason, str) and reason.startswith(prefix) else None
