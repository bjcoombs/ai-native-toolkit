"""The toolkit version a run stamps: the plugin's ``plugin.json``, else the standalone skill's version file (#1166).

The plugin carries its version in ``.claude-plugin/plugin.json`` at the repository root. The
standalone skill ZIP has no such file, so its build writes ``plugin-version.json`` (``{"version":
"<x.y.z>"}``) at the skill root instead (``scripts/standalone_skill_config.py``,
``generated_files``). ``read_version`` reads ``plugin.json`` first, so a plugin install stamps
exactly what it stamped before, then the version file, then gives ``unknown``.

Never raises: a missing, unreadable, malformed or non-object file reads as absent. The version
file is never committed in this repository (``.gitignore``); only the ZIP build writes it.

Readers: ``lib.run.run_setup.read_plugin_version`` (run context, wiki, report),
``lib.run.signals.read_plugin_version`` (the signals and fleet stamp), ``complexity-treemap.py``
(the stats sidecar) and ``assess_emit_workflow.py`` (the pinned action version). Tests:
``tests/test_plugin_version.py``. Stdlib only, so the lightweight scripts can import it.
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = ["SKILL_ROOT", "UNKNOWN", "VERSION_FILE", "read_version"]

UNKNOWN = "unknown"
# skills/assess/, whether in the repository or unpacked from the standalone ZIP.
SKILL_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = SKILL_ROOT / "plugin-version.json"


def _version_in(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return str(version) if version else None


def read_version(plugin_json: Path, version_file: Path | None = None) -> str:
    """``plugin_json``'s ``version``, else ``version_file``'s (default ``VERSION_FILE``), else ``unknown``."""
    return _version_in(plugin_json) or _version_in(version_file or VERSION_FILE) or UNKNOWN
