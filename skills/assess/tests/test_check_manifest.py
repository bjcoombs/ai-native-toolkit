"""The core check manifest agrees with the tables it describes, in both directions (#792).

``skills/assess/checks/core.toml`` is checked against the scan registry
(``scan_registry.SCANS``), the headless runner's tables (``signals``), the typed roots
(``counts_only_schema.ROOTS``), the counts-only rules (``counts_only_rules.RULES``),
the finding tables (``keyhole_signals``, ``assess_config``), the default maturity
model and the signal classes of ``level_signals``. Each failure names the scan,
signal, condition or finding that disagrees. Until later #789 steps generate these
tables from the manifest, a new scan, criterion, finding or counts-only rule needs a
manifest entry too, and these tests say which one is missing.
"""
from __future__ import annotations

import importlib
import json
import tomllib
from collections.abc import Iterable
from functools import partial
from pathlib import Path
from typing import Any, cast, get_args

import jsonschema
import pytest

from lib import check_manifest as cm
from lib import scan_registry as sr
from lib import signals as sg
from lib.assess_config import GATE_CONCERN_FINDINGS
from lib.coupling_analysis import (DEFAULT_HIGH_FRONT_DOOR_RATIO, DEFAULT_HIGH_MODULARITY_Q,
                                   DEFAULT_LOW_CONTAINMENT)
from lib.counts_only_rules import RULES, FieldClass
from lib.counts_only_schema import ROOTS
from lib.keyhole_signals import (FINDING_ACTIONS, FINDING_MODE_VALUES, FINDING_MODES,
                                 FINDING_ORDER, GIT_HISTORY_FINDINGS, _CHURN_DERIVED_FINDINGS)
from lib.level_signals import SIGNAL_INPUTS, InputClass, load_default_levels

SCHEMA = Path(__file__).resolve().parents[1] / "references" / "check-manifest.schema.json"
GATE_TOKENS = {sr._SETUP: "setup", sr._BAD_SCOPE: "bad_scope",
               sr._SELF_DEGRADING: "self_degrading", sr._SERIALISER: "serialiser"}


@pytest.fixture(scope="module")
def manifest() -> cm.CheckManifest:
    return cm.load_manifest()


@pytest.fixture(scope="module")
def raw() -> cm.CheckManifest:
    """The manifest as parsed TOML, unvalidated: the registry comparison still names a scan
    whose entry is missing when a dangling reference stops the strict loader."""
    return cast(cm.CheckManifest, tomllib.loads(cm.CORE_MANIFEST.read_text(encoding="utf-8")))


def _clean(problems: Iterable[str]) -> None:
    found = list(problems)
    assert not found, "check manifest disagrees:\n  " + "\n  ".join(found)


def _resolve(ref: str) -> Any:
    module, attr = ref.split(":")
    return getattr(importlib.import_module(module), attr)


def _fn(fn: Any) -> Any:
    return fn.func if isinstance(fn, partial) else fn


def _published(spec: sr.ScanSpec) -> list[str]:
    return list(spec.spread) if spec.spread else ([] if spec.private else [spec.key])


def test_core_manifest_validates_against_the_schema(manifest: cm.CheckManifest) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(json.loads(json.dumps(manifest)), schema)


def test_registry_matches_manifest(raw: cm.CheckManifest) -> None:
    """Every ScanSpec has a collector entry with equal metadata, in table order, and back."""
    entries = {cm.scan_key(c): c for c in cm.collectors(raw)}
    specs = {spec.key: spec for spec in sr.SCANS}
    problems = [f"{key}: registry scan has no manifest collector entry"
                for key in specs if key not in entries]
    problems += [f"{key}: manifest collector entry has no registry scan"
                 for key in entries if key not in specs]
    for key in specs.keys() & entries.keys():
        spec, c = specs[key], entries[key]
        want: dict[str, object] = {
            "callable": f"{_fn(spec.fn).__module__}:{_fn(spec.fn).__name__}",
            "reads": list(spec.reads), "stage": spec.stage, "publishes": _published(spec),
            "private": spec.private, "spread": bool(spec.spread), "degrade": spec.degrade,
            "gate_reason": GATE_TOKENS.get(spec.gate_reason), "label": spec.label or None,
            "network": spec.network,
            "offline_callable": None if spec.offline_fn is None else
            f"{_fn(spec.offline_fn).__module__}:{_fn(spec.offline_fn).__name__}",
        }
        defaults: dict[str, object] = {"private": False, "spread": False, "degrade": True,
                                       "network": False}
        for field, value in want.items():
            got = c.get(field, defaults.get(field))
            if got != value:
                problems.append(f"{key}: {field} is {got!r} in the manifest, {value!r} in the registry")
    order = [k for k in entries if k in specs]
    if not problems and order != [spec.key for spec in sr.SCANS]:
        problems.append("collector entries are not in scan-table order")
    _clean(problems)


def test_callables_resolve_to_the_registered_functions(manifest: cm.CheckManifest) -> None:
    specs = {spec.key: spec for spec in sr.SCANS}
    for c in cm.collectors(manifest):
        spec = specs[cm.scan_key(c)]
        assert _resolve(c["callable"]) is _fn(spec.fn), cm.scan_key(c)
        if "offline_callable" in c:
            assert _resolve(c["offline_callable"]) is _fn(spec.offline_fn), cm.scan_key(c)
    for f in cm.findings(manifest):
        if "derive" in f:
            assert callable(_resolve(f["derive"])), f["name"]


def test_headless_tables_match_manifest(manifest: cm.CheckManifest) -> None:
    status: dict[str, tuple[str, str]] = {}
    variants: list[tuple[str, cm.HeadlessVariant]] = []
    for c in cm.collectors(manifest):
        status.update({b: (cm.scan_key(c), s) for b, s in c.get("headless", {}).items()})
        variants += [(cm.scan_key(c), v) for v in c.get("headless_variants", [])]
    problems = []
    unregistered = {b for b, (_, s) in status.items() if s == "unregistered"}
    problems += [f"{b}: in UNREGISTERED_SCANS, not unregistered in the manifest"
                 for b in sg.UNREGISTERED_SCANS if b not in unregistered]
    problems += [f"{b}: unregistered in the manifest, not in UNREGISTERED_SCANS"
                 for b in sorted(unregistered - set(sg.UNREGISTERED_SCANS))]
    # Compared by block: HEADLESS_SCANS order is the headless runner's, not the scan table's.
    got = {v["block"]: (v["callable"], v["reads"], v["stage"]) for _, v in variants}
    want = {s.key: (f"{_fn(s.fn).__module__}:{_fn(s.fn).__name__}", list(s.reads), s.stage)
            for s in sg.HEADLESS_SCANS}
    problems += [f"{b}: headless variant {got.get(b)} in the manifest, {want.get(b)} in "
                 f"signals.HEADLESS_SCANS" for b in sorted(got.keys() | want.keys())
                 if got.get(b) != want.get(b)]
    variant_keys = {s.key for s in sg.HEADLESS_SCANS}
    reported = {s.key for s in sg.SIGNAL_SCANS if sg._reported(s)} - variant_keys
    registered = {b for b, (_, s) in status.items() if s == "registered"}
    problems += [f"{b}: reported by the headless runner, not registered in the manifest"
                 for b in sorted(reported - registered)]
    problems += [f"{b}: registered in the manifest, not reported by the headless runner"
                 for b in sorted(registered - reported)]
    for flag, table in (("platform_pass", sg.PLATFORM_SCANS), ("platform_only", sg.PLATFORM_ONLY_SCANS)):
        flagged = {cm.scan_key(c) for c in cm.collectors(manifest) if c.get(flag)}
        problems += [f"{k}: {flag} disagrees with signals" for k in sorted(flagged ^ set(table))]
    problems += [f"{name}: known to the headless runner, no manifest block" for name in sg.known_scans()
                 if name not in status]
    _clean(problems)


def test_shapes_match_typed_roots(manifest: cm.CheckManifest) -> None:
    got = {(s["path"], s["type"]) for c in cm.collectors(manifest) for s in c.get("shapes", [])}
    want = {(path, f"{tp.__module__}:{tp.__name__}") for path, tp in ROOTS}
    _clean([f"{path}: typed root {tp} has no manifest shape" for path, tp in sorted(want - got)]
           + [f"{path}: manifest shape {tp} is not in ROOTS" for path, tp in sorted(got - want)])
    for path, tp in got:
        assert (path, _resolve(tp)) in ROOTS, path


def test_privacy_matches_counts_only_rules(manifest: cm.CheckManifest) -> None:
    want = {pattern: cls for group in RULES.values() for pattern, cls in group.items()}
    got: dict[str, str] = {}
    problems = []
    for c in cm.collectors(manifest):
        for pattern, cls in c.get("privacy", {}).items():
            if pattern in got:
                problems.append(f"{pattern}: classified by two collectors")
            got[pattern] = cls
    problems += [f"{p}: in counts_only_rules.RULES, in no collector's privacy table"
                 for p in want if p not in got]
    problems += [f"{p}: in a privacy table, not in counts_only_rules.RULES" for p in got if p not in want]
    problems += [f"{p}: {got[p]!r} in the manifest, {want[p]!r} in RULES"
                 for p in want.keys() & got.keys() if got[p] != want[p]]
    _clean(problems)


def _criteria() -> list[tuple[str, int, str, object]]:
    model = load_default_levels()
    return [(row["id"], lvl["level"], c["signal"], c["value"])
            for row in model["rows"] for lvl in row["levels"] for c in lvl["criteria"]]


def test_signals_match_default_model(manifest: cm.CheckManifest) -> None:
    entries = {s["path"]: s for s in cm.signals(manifest)}
    read = {sig: value for _, _, sig, value in _criteria()}
    problems = [f"{sig}: read by default.toml, no signal entry" for sig in read if sig not in entries]
    problems += [f"{sig}: signal entry read by no default criterion" for sig in entries if sig not in read]
    for sig in read.keys() & entries.keys():
        kind = "bool" if isinstance(read[sig], bool) else "number"
        if entries[sig]["type"] != kind:
            problems.append(f"{sig}: type {entries[sig]['type']!r}, the criterion compares a {kind}")
    _clean(problems)


def test_signal_evidence_matches_level_signals(manifest: cm.CheckManifest) -> None:
    """The evidence class is level_signals.SIGNAL_INPUTS (#783); the grade derives from it."""
    got = {s["path"]: s["evidence"] for s in cm.signals(manifest)}
    _clean([f"{p}: evidence {got.get(p)!r} in the manifest, {cls!r} in SIGNAL_INPUTS"
            for p, cls in SIGNAL_INPUTS.items() if got.get(p) != cls]
           + [f"{p}: no SIGNAL_INPUTS class" for p in got if p not in SIGNAL_INPUTS])
    assert set(cm.EVIDENCE) == set(get_args(InputClass))


def test_conditions_match_default_model(manifest: cm.CheckManifest) -> None:
    want = [cm.condition_key(row, level, sig) for row, level, sig, _ in _criteria()]
    got = [c["key"] for c in cm.conditions(manifest)]
    _clean([f"{k}: default criterion has no condition entry" for k in want if k not in got]
           + [f"{k}: condition entry has no default criterion" for k in got if k not in want])
    assert len(set(want)) == len(want), "a condition key is not unique in default.toml"


def test_findings_match_finding_tables(manifest: cm.CheckManifest) -> None:
    entries = cm.findings(manifest)
    assert [f["name"] for f in entries] == FINDING_ORDER
    problems = []
    for f in entries:
        name = f["name"]
        checks = {
            "action": (f["action"], FINDING_ACTIONS[name]),
            "mode": (f["mode"], FINDING_MODES[name]),
            "concern": (f["concern"], name in GATE_CONCERN_FINDINGS),
            "prune_missing_paths": (f.get("prune_missing_paths", False), name in GIT_HISTORY_FINDINGS),
            "suppressed_when": ("suppressed_when" in f, name in _CHURN_DERIVED_FINDINGS),
        }
        problems += [f"{name}: {field} is {got!r} in the manifest, {want!r} in code"
                     for field, (got, want) in checks.items() if got != want]
    _clean(problems)
    hidden = next(f for f in entries if f["name"] == "hidden_coupling")
    assert hidden["thresholds"] == {"low_containment": DEFAULT_LOW_CONTAINMENT,
                                    "high_modularity_q": DEFAULT_HIGH_MODULARITY_Q,
                                    "high_front_door_ratio": DEFAULT_HIGH_FRONT_DOOR_RATIO}


def test_loader_vocabularies_match_the_code() -> None:
    assert cm.STAGES == sr.STAGES
    assert cm.PROVIDED_INPUTS == sr.PROVIDED_INPUTS
    assert set(GATE_TOKENS) == sr.GATE_REASONS and set(GATE_TOKENS.values()) == set(cm.GATE_REASONS)
    assert set(cm.FINDING_MODES) == FINDING_MODE_VALUES
    assert set(cm.PRIVACY_CLASSES) == set(get_args(FieldClass))


def test_schema_and_loader_declare_the_same_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    assert set(defs["entry"]["properties"]) == set(cm._COMMON)
    for kind in cm.KINDS:
        assert set(defs[kind]["properties"]) == set(cm._FIELDS[kind]), kind
        assert set(defs[kind]["required"]) == cm._REQUIRED[kind], kind
    assert set(schema["properties"]) == set(cm._ROOT)
    assert defs["collector"]["properties"]["stage"]["enum"] == list(cm.STAGES)
    assert defs["evidence"]["enum"] == list(cm.EVIDENCE)
    assert defs["privacy"]["enum"] == list(cm.PRIVACY_CLASSES)
    assert defs["finding"]["properties"]["mode"]["enum"] == list(cm.FINDING_MODES)
    assert defs["collector"]["properties"]["gate_reason"]["enum"] == list(cm.GATE_REASONS)
