"""The core check manifest agrees with the tables it describes, in both directions (#792).

``skills/assess/checks/core.toml`` is checked against the scan registry
(``scan_registry.SCANS``), the headless runner's tables (``signals``), the typed roots
(``counts_only_schema.ROOTS``), the default maturity model and the signal classes of
``level_signals``. Each failure names the scan, signal, condition or finding that
disagrees. Until later #789 steps generate those tables from the manifest, a new scan or
criterion needs a manifest entry too, and these tests say which one is missing.

The finding tables, the ``hidden_coupling`` thresholds and the counts-only ``RULES``
are generated from the manifest (#795), so they agree by construction; the tests here
pin the builders (an edited manifest changes the table) and the loader rules the
generated tables rely on.
"""
from __future__ import annotations

import copy
import importlib
import json
import re
import types
from collections.abc import Iterable
from functools import partial
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin, get_type_hints, is_typeddict

import jsonschema
import pytest

import lib.manifest.check_manifest as cm
from lib.run import scan_registry as sr
from lib.run import signals as sg
import lib.keyhole.coupling_analysis as ca
from lib.assess_config import GATE_CONCERN_FINDINGS
from lib.counts_only.counts_only_rules import RULES, FieldClass
from lib.counts_only.counts_only_schema import ROOTS
from lib.graph.import_graph import STRUCTURE_REASON_CODES
from lib.keyhole.keyhole_signals import (FINDING_ACTIONS, FINDING_MODE_VALUES, FINDING_MODES,
                                 FINDING_ORDER, GIT_HISTORY_FINDINGS, _CHURN_DERIVED_FINDINGS)
from lib.scoring.level_signals import STAGED_BLOCKS, InputClass, load_default_levels

SCHEMA = Path(__file__).resolve().parents[1] / "references" / "check-manifest.schema.json"
SIGNALS_SCHEMA = SCHEMA.with_name("signals.schema.json")
GATE_TOKENS = {sr._SETUP: "setup", sr._BAD_SCOPE: "bad_scope",
               sr._SELF_DEGRADING: "self_degrading", sr._SERIALISER: "serialiser"}


@pytest.fixture(scope="module")
def manifest() -> cm.CheckManifest:
    return cm.load_manifest()


@pytest.fixture(scope="module")
def raw() -> cm.CheckManifest:
    """The manifest as parsed TOML, unvalidated: the registry comparison still names a scan
    whose entry is missing when a dangling reference stops the strict loader."""
    return cast(cm.CheckManifest, cm.read_manifest())


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
    # Every "lib.x:attr" string in core.toml or a checks/core/ fragment, in any
    # table, names the module that holds attr: a #917 alias shim (another
    # __name__) or a package __getattr__ forward (attr not in the module's
    # namespace) still resolves, so a stale path would otherwise pass.
    files = [cm.CORE_MANIFEST, *sorted(cm.CORE_MANIFEST.with_suffix("").glob("*.toml"))]
    refs = [ref for path in files
            for ref in re.findall(r"""["'](lib(?:\.\w+)+):(\w+)["']""", path.read_text(encoding="utf-8"))]
    assert len(refs) >= 90, refs
    for module_name, attr in refs:
        module = importlib.import_module(module_name)
        assert module.__name__ == module_name, f"{module_name}:{attr} resolves through a shim"
        assert attr in vars(module), f"{module_name}:{attr} is not defined in {module_name}"
        # A re-export (``from lib.nested.worker import scan`` in lib.worker) is in
        # the namespace too: a callable must be defined by the module it names.
        value = _fn(vars(module)[attr])
        if callable(value):
            assert value.__module__ == module_name, (
                f"{module_name}:{attr} is defined in {value.__module__}, not {module_name}")


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


def _names_a_field(path: str) -> bool:
    """True when ``path`` walks a typed root field by field (an object or list may end it)."""
    parts = path.split(".")
    for cut in range(1, len(parts) + 1):
        shapes = [tp for root, tp in ROOTS if root == ".".join(parts[:cut])]
        if shapes:
            return any(_walks(tp, parts[cut:]) for tp in shapes)
    return True  # not under a typed root: only the first segment is checked


def _walks(tp: Any, rest: list[str]) -> bool:
    if not rest:
        return True
    origin = get_origin(tp)
    if origin is list or origin in (Union, types.UnionType):
        return any(_walks(arg, rest) for arg in get_args(tp))
    if origin is dict:
        return _walks(get_args(tp)[1], rest[1:])
    if is_typeddict(tp):
        hints = get_type_hints(tp)
        return rest[0] in hints and _walks(hints[rest[0]], rest[1:])
    return False


def test_finding_inputs_under_a_typed_root_name_its_fields(manifest: cm.CheckManifest) -> None:
    """A finding input under a typed root (``structure_drift.tier_1`` since #793) names a
    real field of its shape, not only a known first segment."""
    _clean([f"{f['name']}: input {read!r} is no field of its typed root"
            for f in cm.findings(manifest) for read in f["inputs"] if not _names_a_field(read)])
    assert not _names_a_field("structure_drift.tier_2")


def _entry(m: cm.CheckManifest, entry_id: str) -> Any:
    return next(e for e in m["entries"] if e["id"] == entry_id)


def test_rules_are_the_privacy_tables(manifest: cm.CheckManifest) -> None:
    """RULES is each collector's privacy table, keyed by scan key in scan-table order."""
    assert RULES == cm.privacy_rules(manifest)
    assert list(RULES) == [cm.scan_key(c) for c in cm.collectors(manifest) if c.get("privacy")]


def test_an_edited_privacy_table_changes_the_rules(manifest: cm.CheckManifest) -> None:
    edited = copy.deepcopy(manifest)
    _entry(edited, "core.ci_checks")["privacy"]["ci_checks.basis"] = "drop"
    assert cm.privacy_rules(manifest)["ci_checks"]["ci_checks.basis"] == "token"
    assert cm.privacy_rules(edited)["ci_checks"]["ci_checks.basis"] == "drop"


def test_a_pattern_classified_by_two_collectors_is_rejected(manifest: cm.CheckManifest) -> None:
    edited = copy.deepcopy(manifest)
    _entry(edited, "core.ai_usage")["privacy"]["ci_checks.basis"] = "drop"
    codes = [e["code"] for e in cm.validate_manifest(cast(dict[str, Any], edited))]
    assert codes == ["DUPLICATE_PRIVACY"]


def _criteria() -> list[tuple[str, int, str, object]]:
    model = load_default_levels()
    return [(row["id"], lvl["level"], c["signal"], c["value"])
            for row in model["rows"] for lvl in row["levels"] for c in lvl["criteria"]]


def test_signals_match_default_model(manifest: cm.CheckManifest) -> None:
    entries = {s["path"]: s for s in cm.signals(manifest)}
    read = {sig: value for _, _, sig, value in _criteria()}
    problems = [f"{sig}: read by default.toml, no signal entry" for sig in read if sig not in entries]
    problems += [f"{sig}: signal entry read by no default criterion" for sig in entries
                 if sig not in read and sig.split(".")[0] not in STAGED_BLOCKS]
    # A staged block (#1322) is allowed only while no default criterion reads it.
    problems += [f"{sig}: read by default.toml, drop {sig.split('.')[0]} from STAGED_BLOCKS"
                 for sig in read if sig.split(".")[0] in STAGED_BLOCKS]
    for sig in read.keys() & entries.keys():
        kind = "bool" if isinstance(read[sig], bool) else "number"
        if entries[sig]["type"] != kind:
            problems.append(f"{sig}: type {entries[sig]['type']!r}, the criterion compares a {kind}")
    _clean(problems)


def test_evidence_vocabulary_is_the_input_classes() -> None:
    """SIGNAL_INPUTS is generated from the evidence (#953); the vocabularies must agree."""
    assert set(cm.EVIDENCE) == set(get_args(InputClass))


def test_conditions_match_default_model(manifest: cm.CheckManifest) -> None:
    want = [cm.condition_key(row, level, sig) for row, level, sig, _ in _criteria()]
    got = [c["key"] for c in cm.conditions(manifest)]
    _clean([f"{k}: default criterion has no condition entry" for k in want if k not in got]
           + [f"{k}: condition entry has no default criterion" for k in got if k not in want])
    assert len(set(want)) == len(want), "a condition key is not unique in default.toml"


def test_finding_tables_are_generated_from_the_manifest(manifest: cm.CheckManifest) -> None:
    tables = cm.finding_tables(manifest)
    assert FINDING_ORDER == tables["order"] == [f["name"] for f in cm.findings(manifest)]
    assert FINDING_ACTIONS == tables["actions"] and FINDING_MODES == tables["modes"]
    assert GATE_CONCERN_FINDINGS == tables["concern"]
    assert GIT_HISTORY_FINDINGS == tables["prune_missing_paths"] == {"hidden_coupling",
                                                                     "refactor_boundary"}
    assert _CHURN_DERIVED_FINDINGS == tables["churn_suppressed"] == {"hidden_coupling", "lying_map"}


def test_every_finding_has_a_path_derivation() -> None:
    """The manifest's finding names are the derivation's keys (a missing key would read empty)."""
    from lib.keyhole.keyhole_signals import _finding_path_inputs
    inputs = _finding_path_inputs(
        behaviour={}, documentation={}, understanding={}, drift_hidden_dirs=[],  # type: ignore[typeddict-item]  # empty dicts stand in for the scan blocks
        churn_degenerate=False, untrusted=[], self_ref_paths=[], unactioned=[],
        accreting_paths=[], dead_weight=[], override_contradiction_paths=[])
    assert list(inputs) == FINDING_ORDER


def test_an_edited_finding_order_reorders_the_tables(manifest: cm.CheckManifest) -> None:
    edited = copy.deepcopy(manifest)
    hidden = _entry(edited, "core.finding.hidden_coupling")
    lying = _entry(edited, "core.finding.lying_map")
    hidden["order"], lying["order"] = lying["order"], hidden["order"]
    lying["concern"] = False
    tables = cm.finding_tables(edited)
    assert tables["order"][:2] == ["lying_map", "hidden_coupling"]
    assert "lying_map" not in tables["concern"]
    assert cm.finding_tables(manifest)["order"][:2] == ["hidden_coupling", "lying_map"]


def test_only_the_churn_expression_is_accepted(manifest: cm.CheckManifest) -> None:
    edited = copy.deepcopy(manifest)
    _entry(edited, "core.finding.lying_map")["suppressed_when"] = "doc_staleness.churn_degenerate"
    codes = [e["code"] for e in cm.validate_manifest(cast(dict[str, Any], edited))]
    assert codes == ["UNSUPPORTED_EXPRESSION"]


def test_coupling_thresholds_come_from_the_manifest(manifest: cm.CheckManifest) -> None:
    """Every threshold is read (none is documentary) and the defaults are the manifest's."""
    thresholds = cm.finding_thresholds(manifest, "hidden_coupling")
    assert thresholds == {"low_containment": ca.DEFAULT_LOW_CONTAINMENT,
                          "high_modularity_q": ca.DEFAULT_HIGH_MODULARITY_Q,
                          "high_front_door_ratio": ca.DEFAULT_HIGH_FRONT_DOOR_RATIO}
    assert [f["name"] for f in cm.findings(manifest) if "thresholds" in f] == ["hidden_coupling"]
    assert cm.finding_thresholds(manifest, "lying_map") == {}


def test_finding_headless_status_is_the_derived_findings_block(manifest: cm.CheckManifest) -> None:
    """A finding is reported headless exactly as the derived_findings block is."""
    owner = next(c for c in cm.collectors(manifest) if "derived_findings" in c["publishes"])
    status = owner.get("headless", {}).get("derived_findings", "registered")
    _clean([f"{f['name']}: headless {f.get('headless')!r}, derived_findings is {status!r}"
            for f in cm.findings(manifest) if f.get("headless", "registered") != status])


def test_structure_reason_codes_match_import_graph(manifest: cm.CheckManifest) -> None:
    """The declared structure codes are STRUCTURE_REASON_CODES (a <name> family by prefix)."""
    declared = [r["code"] for r in cm.reason_codes(manifest, "structure")]
    families = [c.split("<")[0] for c in declared if "<" in c]
    exact = {c for c in declared if "<" not in c}

    def covered(code: str) -> bool:
        return code in exact or any(code.startswith(f) for f in families)
    _clean([f"{c}: a structure reason code with no manifest entry"
            for c in sorted(STRUCTURE_REASON_CODES) if not covered(c)]
           + [f"{c}: declared, not in import_graph.STRUCTURE_REASON_CODES"
              for c in sorted(exact - STRUCTURE_REASON_CODES)]
           + [f"{f}<...>: a family no code belongs to" for f in families
              if not any(c.startswith(f) for c in STRUCTURE_REASON_CODES - exact)])
    assert cm.reason_codes(manifest, "behaviour") == []
    assert cm.reason_codes(manifest, "ci_checks") == []  # included_jobs is carried in enforced_basis


def test_enforced_basis_codes_are_carried_in_enforced_basis(manifest: cm.CheckManifest) -> None:
    """A ci_checks code enforced_basis can hold is declared ``carried_in = "enforced_basis"``,
    and a code declared there is one enforced_basis holds (#957 item 3, #1031)."""
    block = json.loads(SIGNALS_SCHEMA.read_text(encoding="utf-8"))["properties"]["scans"]["properties"]["ci_checks"]
    held = set(block["properties"]["enforced_basis"]["enum"])
    declared = {r["code"]: r.get("carried_in", "reason")
                for r in _entry(manifest, "core.ci_checks").get("unknown_reasons", [])}
    _clean([f"{c}: enforced_basis holds it, carried in {where}"
            for c, where in sorted(declared.items()) if c in held and where != "enforced_basis"]
           + [f"{c}: carried in enforced_basis, which never holds it"
              for c, where in sorted(declared.items())
              if where == "enforced_basis" and c not in held])


def test_a_reason_for_an_unpublished_block_is_rejected(manifest: cm.CheckManifest) -> None:
    edited = copy.deepcopy(manifest)
    _entry(edited, "core.keyhole_blocks")["unknown_reasons"][0]["block"] = "structur"
    errors = cm.validate_manifest(cast(dict[str, Any], edited))
    assert [e["code"] for e in errors] == ["UNPUBLISHED_BLOCK"]
    assert errors[0].get("suggestion") == "structure"


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
    carried = defs["unknown_reason"]["properties"]
    assert carried["carried_in"]["enum"] == list(cm.CARRIED_IN)
    assert get_args(cm.CarriedIn) == cm.CARRIED_IN
