"""SIGNAL_INPUTS and the ScanSpec metadata are generated from checks/core.toml (#953)."""
from __future__ import annotations

import ast
import copy
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

import lib.manifest.check_manifest as cm
from lib import scan_registry as sr
from lib.level_tally import LEVEL_READS
from lib.signal_grades import INPUT_GRADES, SIGNAL_INPUTS

LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib"
SIGNAL = "review_reality.reviewed_share"


def _raw() -> cm.CheckManifest:
    return cast(cm.CheckManifest, copy.deepcopy(cm.read_manifest()))


def _entry(m: cm.CheckManifest, kind: str, field: str, value: str) -> Any:
    return next(e for e in m["entries"] if e["kind"] == kind and e.get(field) == value)


def _grade(table: dict[str, str], signal: str) -> str | None:
    cls = table.get(signal)
    return None if cls is None else INPUT_GRADES[cast(Any, cls)]


def test_signal_inputs_are_the_manifest_evidence() -> None:
    assert SIGNAL_INPUTS == cm.signal_inputs(cm.core_manifest())


def test_removing_a_signal_evidence_line_fails_the_load(tmp_path: Path) -> None:
    """``evidence`` is required, so no class means no load: SIGNAL_INPUTS is built through it."""
    checks = tmp_path / "checks"
    shutil.copytree(cm.CORE_MANIFEST.parent, checks)
    fragment = checks / "core" / "review_reality.toml"
    text = fragment.read_text(encoding="utf-8")
    line = 'path = "review_reality.reviewed_share"\ntype = "number"\nevidence = "platform"\n'
    assert text.count(line) == 1
    fragment.write_text(text.replace(line, line.removesuffix('evidence = "platform"\n')),
                        encoding="utf-8")
    with pytest.raises(cm.CheckManifestError) as err:
        cm.load_manifest(checks / "core.toml")
    assert any(e["code"] == "MISSING_KEY" and "evidence" in str(e) for e in err.value.errors)


def test_editing_a_signal_evidence_line_changes_its_grade() -> None:
    m = _raw()
    assert _grade(cm.signal_inputs(cm.parse_manifest(m)), SIGNAL) == "verified"
    _entry(m, "signal", "path", SIGNAL)["evidence"] = "head_text"
    assert _grade(cm.signal_inputs(cm.parse_manifest(m)), SIGNAL) == "declared"


def test_every_scan_spec_is_built_from_its_collector_entry() -> None:
    m = cm.core_manifest()
    for spec in sr.SCANS:
        assert sr.spec_from_manifest(m, spec.key, spec.fn, spread=spec.spread,
                                     offline_fn=spec.offline_fn) == spec


def test_changing_a_collector_reads_changes_its_spec() -> None:
    m = _raw()
    _entry(m, "collector", "id", "core.structure_scan")["reads"] = ["repo_root"]
    spec = sr.spec_from_manifest(m, "structure_scan", lambda root: None)
    assert spec.reads == ("repo_root",)
    assert (spec.stage, spec.private, spec.label) == ("read_side", True, "structure")


def test_gate_reason_and_network_come_from_the_entry() -> None:
    m = _raw()
    entry = _entry(m, "collector", "id", "core.agent_ops")
    entry.update(degrade=False, gate_reason="serialiser", network=True)
    spec = sr.spec_from_manifest(m, "agent_ops", lambda root: None)
    assert (spec.degrade, spec.gate_reason, spec.network) == (False, sr._SERIALISER, True)


def test_every_gate_token_has_its_text() -> None:
    assert tuple(sr.GATE_REASON_TEXT) == cm.GATE_REASONS
    assert frozenset(sr.GATE_REASON_TEXT.values()) == sr.GATE_REASONS


def test_a_scan_without_a_collector_entry_is_rejected() -> None:
    with pytest.raises(sr.ScanRegistryError, match="no collector entry"):
        sr.spec_from_manifest(cm.core_manifest(), "not_a_scan", lambda: None)


def test_a_spread_the_entry_does_not_declare_is_rejected() -> None:
    with pytest.raises(sr.ScanRegistryError, match="spread"):
        sr.spec_from_manifest(cm.core_manifest(), "agent_ops", lambda root: None, spread=("x",))


def _imported(source: str) -> set[str]:
    """Every ``lib`` module a source imports: plain, ``from`` and package-relative imports."""
    names: set[str] = set()
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:  # relative: lib/ is flat, so any level resolves inside lib
                base = f"lib.{base}" if base else "lib"
            names.update([base] if base != "lib" else [f"lib.{a.name}" for a in n.names])
    return {name for name in names if name == "lib" or name.startswith("lib.")}


def _lib_imports(module: str) -> set[str]:
    return _imported((LIB / f"{module}.py").read_text(encoding="utf-8"))


def test_the_import_guard_sees_every_import_form() -> None:
    assert _imported("from lib.manifest.check_manifest import core_manifest") == {"lib.manifest.check_manifest"}
    assert _imported("import lib.core_scans") == {"lib.core_scans"}
    assert _imported("from lib import core_scans") == {"lib.core_scans"}
    assert _imported("from . import core_scans") == {"lib.core_scans"}
    assert _imported("from .core_scans import level") == {"lib.core_scans"}


def test_signal_grades_reaches_only_the_manifest_loader() -> None:
    """The tally engine reads signal_grades without importing a scanner (#744, #953)."""
    assert _lib_imports("signal_grades") == {"lib.manifest.check_manifest"}
    assert _lib_imports("manifest/check_manifest") == {"lib.manifest.check_validation", "lib.manifest_whys"}
    # The why rules (#939) reach only stdlib-only leaf modules, so the closure stays bounded.
    assert _lib_imports("manifest_whys") == {"lib.manifest.check_validation", "lib.levels_fields", "lib.vocabulary"}
    for leaf in ("manifest/check_validation", "levels_fields", "vocabulary"):
        assert _lib_imports(leaf) == set(), leaf


def test_level_reads_are_pinned_to_the_tally_order() -> None:
    """core_scans.level zips its blocks with LEVEL_READS, so the manifest list must match."""
    level = next(s for s in sr.SCANS if s.key == "level")
    assert sr.pinned_reads(level, LEVEL_READS) is level
    swapped = (LEVEL_READS[1], LEVEL_READS[0], *LEVEL_READS[2:])
    with pytest.raises(sr.ScanRegistryError, match="zips"):
        sr.pinned_reads(level, swapped)
