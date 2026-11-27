"""The scan spec: one declared run-context scan, and the checks on a table of them.

Split out of ``lib/run/scan_registry.py`` (#848), which keeps the table (``SCANS``) and the
loop that runs it (``run_scans``). This module holds what a table entry is and how one is
checked: ``ScanSpec``, the stage names (``STAGES``), the core-provided inputs
(``PROVIDED_INPUTS``), ``validate`` (the import-time check on a whole table, raising
``ScanRegistryError``), the degrade opt-out reasons (``GATE_REASONS``, ``GATE_REASON_TEXT``),
``spec_from_manifest`` (an entry's metadata read from its collector entry in
``checks/core/<key>.toml``, #953, with each read bound by name, ``lib/run/scan_binding.py``,
#1049) and ``pinned_reads``. ``scan_registry`` re-exports every name, so callers import them
from either module. Tests: ``tests/test_scan_registry.py``.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import lib.manifest.check_manifest as cm
from lib.run.scan_binding import binding_error

# Names the core passes to ``run_scans``: the arguments of
# ``build_run_context``. A spec may read these, or the key of any spec declared
# before it (``instruction_files``, ``scope``, ``excludes``, ``history_window`` and
# ``fleet_config`` are setup specs). ``plan`` is the run's ``lib.run.scan_plan.ScanPlan``, or
# None for a full run (#1268).
PROVIDED_INPUTS = frozenset({"repo_root", "requested_scope", "run_date", "non_interactive",
                             "maturity_model", "managed_settings", "plan", "packs"})

STAGE_SETUP = "setup"
STAGE_READ_SIDE = "read_side"
STAGE_POST_OFFERS = "post_offers"
STAGE_FINAL = "final"
STAGES = (STAGE_SETUP, STAGE_READ_SIDE, STAGE_POST_OFFERS, STAGE_FINAL)

class ScanRegistryError(ValueError):
    """The scan table is malformed (duplicate key, unknown stage or read)."""


@dataclass(frozen=True)
class ScanSpec:
    """One run-context block.

    ``fn`` is called with the values of ``reads``, positionally and in order.
    ``degrade`` routes the call through ``safe``; a scan opts out only by
    setting ``gate_reason``, because a gate is the one kind of scan whose
    failure should stop the run. ``label`` names the scan in a degrade reason
    when it differs from ``key``. ``private`` and ``spread`` are described in
    the module docstring; a degraded spread spec marks every key it declares.

    ``network`` marks a scan that reads beyond the checkout (GitHub via
    ``gh``). The headless signals runner (``lib/run/signals.py``) never calls such
    a scan's ``fn``: it calls ``offline_fn`` when one is declared - the same
    scan with its network probe turned off, taking the same reads - and
    otherwise reports the scan unavailable. ``assess_core`` ignores both fields.
    """

    key: str
    fn: Callable[..., Any]
    reads: tuple[str, ...]
    stage: str
    degrade: bool = True
    gate_reason: str = ""
    label: str = ""
    private: bool = False
    spread: tuple[str, ...] = ()
    network: bool = False
    offline_fn: Callable[..., Any] | None = None


def _accepts(fn: Callable[..., Any], count: int) -> bool:
    """True when ``fn`` can be called with ``count`` positional arguments.

    A callable whose signature cannot be inspected is given the benefit of the
    doubt; the mismatch this guards against is a declared table entry, which is
    always a plain function.
    """
    try:
        inspect.signature(fn).bind(*[None] * count)
    except TypeError:
        return False
    except ValueError:
        return True
    return True


def validate(specs: Iterable[ScanSpec], provided: Iterable[str] = PROVIDED_INPUTS) -> None:
    """Raise ``ScanRegistryError`` unless every spec can run in table order.

    Stages run in ``STAGES`` order, so a read must name a provided input or a
    key whose producer is declared earlier *and* runs at the same or an earlier
    stage; a key a spread entry copies into the run context counts as produced by it,
    and reads None when that entry leaves it out. The run-context keys the table writes (published keys and spread
    keys) must be distinct.
    """
    produced_at: dict[str, int] = {name: -1 for name in provided}
    written: set[str] = set()
    for spec in specs:
        if spec.key in produced_at:
            raise ScanRegistryError(f"scan {spec.key!r} is declared twice or shadows a provided input")
        if spec.stage not in STAGES:
            raise ScanRegistryError(f"scan {spec.key!r} names unknown stage {spec.stage!r}")
        stage_index = STAGES.index(spec.stage)
        missing = [name for name in spec.reads if name not in produced_at]
        if missing:
            raise ScanRegistryError(
                f"scan {spec.key!r} reads {missing}, which no earlier scan produces "
                f"and the core does not provide"
            )
        late = [name for name in spec.reads if produced_at[name] > stage_index]
        if late:
            raise ScanRegistryError(
                f"scan {spec.key!r} runs at stage {spec.stage!r} but reads {late}, "
                f"produced at a later stage"
            )
        if not _accepts(spec.fn, len(spec.reads)):
            raise ScanRegistryError(
                f"scan {spec.key!r} declares {len(spec.reads)} read(s) but its callable "
                f"cannot be called with that many positional arguments"
            )
        if spec.offline_fn is not None and not spec.network:
            raise ScanRegistryError(f"scan {spec.key!r} declares an offline_fn but is not a network scan")
        if spec.offline_fn is not None and not _accepts(spec.offline_fn, len(spec.reads)):
            raise ScanRegistryError(
                f"scan {spec.key!r} declares {len(spec.reads)} read(s) but its offline_fn "
                f"cannot be called with that many positional arguments"
            )
        if not spec.degrade and not spec.gate_reason:
            raise ScanRegistryError(f"scan {spec.key!r} opts out of degrading without a gate_reason")
        if spec.private and spec.spread:
            raise ScanRegistryError(f"scan {spec.key!r} is both private and spread")
        keys = spec.spread if spec.spread else (() if spec.private else (spec.key,))
        clash = sorted({k for k in keys if k in written} | {k for k in keys if keys.count(k) > 1})
        if clash:
            raise ScanRegistryError(f"scan {spec.key!r} writes run-context key(s) {clash} written elsewhere")
        written.update(keys)
        produced_at[spec.key] = stage_index
        # a spread key is readable too: it is in the run context from that stage on
        for name in spec.spread:
            produced_at[name] = stage_index


# The reasons an entry may give for opting out of the degrade wrapper (#418
# requirement 3, #623). Every other entry degrades: its readers accept the
# unavailable marker in place of its usual shape.
_SETUP = (
    "run setup: later entries and the publish step read this value, so a "
    "failure stops the run rather than feeding them an unavailable marker"
)
_BAD_SCOPE = "an invalid --scope is a usage error the CLI reports with exit code 2"
_SELF_DEGRADING = "degrades internally to an explicit unavailable result; a raise here is a bug"
_SERIALISER = "copies fields out of an earlier result that already carries its own degrade"
GATE_REASONS = frozenset({_SETUP, _BAD_SCOPE, _SELF_DEGRADING, _SERIALISER})
# The manifest's ``gate_reason`` token -> the reason text (``check_manifest.GATE_REASONS``).
GATE_REASON_TEXT = {"setup": _SETUP, "bad_scope": _BAD_SCOPE,
                    "self_degrading": _SELF_DEGRADING, "serialiser": _SERIALISER}


def spec_from_manifest(manifest: cm.CheckManifest, key: str, fn: Callable[..., Any], *,
                       spread: tuple[str, ...] = (),
                       offline_fn: Callable[..., Any] | None = None) -> ScanSpec:
    """The ``ScanSpec`` for ``key``, its metadata read from the key's collector entry (#953).

    ``reads``, ``stage``, ``degrade``, ``gate_reason``, ``label``, ``private`` and
    ``network`` come from the manifest; the callables, the spread keys and the table order
    stay in code. A key with no collector entry, or a spread that disagrees with the
    entry's ``spread`` flag, or a read not bound by name (#1049), raises ``ScanRegistryError``.
    """
    entry = next((c for c in cm.collectors(manifest) if cm.scan_key(c) == key), None)
    if entry is None:
        raise ScanRegistryError(f"scan {key!r} has no collector entry in the check manifest")
    if bool(spread) != entry.get("spread", False):
        raise ScanRegistryError(f"scan {key!r}: spread keys disagree with the manifest's spread flag")
    gate, reads = entry.get("gate_reason"), tuple(entry["reads"])
    if (unbound := binding_error(key, reads, fn, offline_fn)) is not None:
        raise ScanRegistryError(unbound)
    return ScanSpec(key, fn, reads, entry["stage"],
                    degrade=entry.get("degrade", True),
                    gate_reason="" if gate is None else GATE_REASON_TEXT[gate],
                    label=entry.get("label", ""), private=entry.get("private", False),
                    spread=spread, network=entry.get("network", False), offline_fn=offline_fn)


def pinned_reads(spec: ScanSpec, reads: tuple[str, ...]) -> ScanSpec:
    """``spec`` unchanged when its manifest ``reads`` are exactly ``reads``, in order; else
    ``ScanRegistryError``. For a callable that zips its arguments with a code tuple."""
    if spec.reads != reads:
        raise ScanRegistryError(f"scan {spec.key!r}: the manifest reads {list(spec.reads)}, "
                                f"but its callable zips them with {list(reads)}")
    return spec
