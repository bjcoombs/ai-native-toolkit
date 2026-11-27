"""A manifest read binds to the scan parameter of the same name (#1049)."""
from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import lib.manifest.check_manifest as cm
from lib.run import run_setup as rs
from lib.run import scan_binding as sb
from lib.run import scan_registry as sr

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DIFF_READS = 'reads = ["prior", "current", "excluded_as_generated"]\n'
REORDERED = 'reads = ["current", "prior", "excluded_as_generated"]\n'
MESSAGE = ("scan 'diff' position 0: the manifest read 'current' does not bind to "
           "parameter 'prior' of its callable")


def _scratch_checks(tmp_path: Path) -> Path:
    """A copy of checks/ whose diff fragment lists stats_diff's reads out of order."""
    checks = tmp_path / "checks"
    shutil.copytree(cm.CORE_MANIFEST.parent, checks)
    fragment = checks / "core" / "diff.toml"
    text = fragment.read_text(encoding="utf-8")
    assert text.count(DIFF_READS) == 1
    fragment.write_text(text.replace(DIFF_READS, REORDERED), encoding="utf-8")
    return checks / "core.toml"


def test_a_reordered_reads_list_is_refused(tmp_path: Path) -> None:
    manifest = cm.load_manifest(_scratch_checks(tmp_path))
    with pytest.raises(sr.ScanRegistryError) as err:
        sr.spec_from_manifest(manifest, "diff", rs.stats_diff)
    assert MESSAGE in str(err.value)


def test_a_reordered_reads_list_fails_the_registry_import(tmp_path: Path) -> None:
    """The registry builds every entry at import, so the scratch fragment stops the import."""
    root = _scratch_checks(tmp_path)
    code = ("import lib.manifest.check_manifest as cm, pathlib\n"
            f"cm.core_manifest = lambda: cm.load_manifest(pathlib.Path({root.as_posix()!r}))\n"
            "import lib.run.scan_registry\n")
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=env, check=False)
    assert run.returncode != 0
    assert "ScanRegistryError" in run.stderr and MESSAGE in run.stderr


def test_every_manifest_scan_binds_by_name() -> None:
    for spec in sr.SCANS:
        assert sb.mismatch(spec.fn, spec.reads) is None, spec.key
        if spec.offline_fn is not None:
            assert sb.mismatch(spec.offline_fn, spec.reads) is None, spec.key


def test_binding_rules() -> None:
    def scan(repo_root: object, _excludes: object, doc_graph_block: object) -> None:
        return None

    assert sb.mismatch(scan, ("repo_root", "excludes", "doc_graph")) is None
    assert sb.mismatch(scan, ("excludes", "repo_root", "doc_graph")) == (0, "excludes", "repo_root")
    # an alias is per read: doc_graph_block is not a name for doc_staleness
    assert sb.mismatch(scan, ("repo_root", "excludes", "doc_staleness")) == (
        2, "doc_staleness", "doc_graph_block")


def test_binding_stops_at_star_args_and_skips_opaque_callables() -> None:
    def zipped(units_block: object, *blocks: object) -> None:
        return None

    assert sb.mismatch(zipped, ("units", "anything", "else")) is None
    assert sb.mismatch(object(), ("whatever",)) is None  # no signature: not checked


def test_an_unused_alias_is_not_kept() -> None:
    """Every alias is used by some scan, so the map cannot grow stale entries."""
    used = {(r, p.name) for spec in sr.SCANS for fn in (spec.fn, spec.offline_fn) if fn is not None
            for r, p in zip(spec.reads, _params(fn))}
    assert set(sb.READ_ALIASES.items()) <= used


def _params(fn: Callable[..., Any]) -> list[inspect.Parameter]:
    try:
        return list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return []
