"""The toolkit version a run stamps (#1166): plugin.json first, then the standalone build's file.

The plugin path must stamp exactly what it stamped before #1166 in every output that carries
it (run context, report header, signals and fleet stamp, stats sidecar), because
``signals.read_plugin_version`` feeds stamped-version comparisons across fleet runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import assess_emit_workflow
from assess_report import made_by
from lib import plugin_version
from lib.run import run_setup, signals

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_plugin_json_wins_over_the_version_file(tmp_path: Path) -> None:
    manifest = _write(tmp_path / "plugin.json", {"version": "1.2.3"})
    built = _write(tmp_path / "plugin-version.json", {"version": "9.9.9"})
    assert plugin_version.read_version(manifest, built) == "1.2.3"


def test_the_version_file_stands_in_when_plugin_json_is_absent(tmp_path: Path) -> None:
    built = _write(tmp_path / "plugin-version.json", {"version": "9.9.9"})
    assert plugin_version.read_version(tmp_path / "missing.json", built) == "9.9.9"
    no_key = _write(tmp_path / "plugin.json", {"name": "x"})
    assert plugin_version.read_version(no_key, built) == "9.9.9"


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '{"version": ""}'])
def test_unreadable_files_read_as_unknown(tmp_path: Path, body: str) -> None:
    (tmp_path / "a.json").write_text(body, encoding="utf-8")
    (tmp_path / "b.json").write_text(body, encoding="utf-8")
    assert plugin_version.read_version(tmp_path / "a.json", tmp_path / "b.json") == "unknown"


def test_the_plugin_path_stamps_what_it_stamped_before() -> None:
    # The version file is never committed, so a plugin checkout reads plugin.json alone.
    assert not plugin_version.VERSION_FILE.exists()
    expected = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    assert run_setup.read_plugin_version() == expected
    assert signals.read_plugin_version() == expected
    assert signals.build_stamp(REPO_ROOT, "6mo")["plugin_version"] == expected
    assert assess_emit_workflow._running_version() == expected
    assert made_by({"plugin_version": expected}) == f"`/assess` v{expected}"


def test_a_standalone_copy_reads_the_version_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_version, "VERSION_FILE", _write(tmp_path / "plugin-version.json", {"version": "4.5.6"}))
    no_toolkit = tmp_path / "toolkit"
    assert run_setup.read_plugin_version(no_toolkit / "plugin.json") == "4.5.6"
    assert signals.read_plugin_version(no_toolkit) == "4.5.6"


def test_the_report_header_leaves_out_an_unknown_version() -> None:
    assert made_by({"plugin_version": "unknown"}) == "`/assess`"
    assert made_by({}) == "`/assess`"
    assert made_by({"plugin_version": "3.0.0"}) == "`/assess` v3.0.0"
