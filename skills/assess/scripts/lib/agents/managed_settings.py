"""Read the machine's Claude Code managed settings for permission and hook guardrails (#1153).

An organisation can set agent guardrails centrally, in managed settings that override every
repository's ``.claude/settings.json``. ``read_managed_settings`` reads the managed sources
Claude Code documents for the platform the check runs on
(https://code.claude.com/docs/en/managed-settings, "Deploy a managed settings file" and
"Choose a delivery mechanism"):

- the file: ``/Library/Application Support/ClaudeCode/managed-settings.json`` (macOS),
  ``/etc/claude-code/managed-settings.json`` (Linux and WSL),
  ``C:\\Program Files\\ClaudeCode\\managed-settings.json`` (Windows);
- the drop-in directory ``managed-settings.d/`` beside it, every ``*.json`` in it;
- macOS: the ``com.anthropic.claudecode`` managed preferences domain, as the plist under
  ``/Library/Managed Preferences/`` (system-wide, then the current user's);
- Windows: the ``Settings`` JSON string under ``HKLM`` then ``HKCU``
  ``SOFTWARE\\Policies\\ClaudeCode``.

Server-managed settings (the claude.ai admin console) are fetched at session start and are not
read here: no file holds them.

The read is opt-in (#1153 decision): it measures the machine, not the repository, so
``lib.agents.agent_ops`` calls it only under ``--managed-settings`` (``assess_core``,
``assess_signals.py``, ``/fleet``) or the ``[agent_ops] managed_settings`` key of a single
repository's ``.assess/config.toml``; otherwise the block is ``not_read()``.

The result is a ``ManagedSettingsBlock``: ``state`` is ``not_read`` (the option was off),
``found`` (at least one source exists and
every one that exists was read), ``absent`` (no source exists) or ``unreadable`` (a source
exists but could not be opened or parsed: permissions, bad JSON, not an object); ``sources``
lists one entry per source that exists, by ``kind`` token only, with its ``allow`` / ``deny`` /
``ask`` entry counts and hook event count. No machine path or setting text is kept.
``allow_count`` leaves out every broad allow (``Bash``, ``Bash(*)`` and the other rules
``lib.agents.harness_settings.is_broad_allow`` matches; the count is that module's
``scoped_allow``, #1621), as the repository count does, so
a managed source whose only entry allows every shell command does not encode a permission.

``ASSESS_MANAGED_SETTINGS_DIR``, when set, replaces every location with that one directory's
``managed-settings.json`` and ``managed-settings.d/``, and no plist or registry read runs: the
test suite points it at an empty directory, so a developer machine's own policy never moves a
golden. Pure stdlib (``plistlib``; ``winreg`` on Windows only). Never raises.
"""
from __future__ import annotations

import importlib
import json
import os
import plistlib
import sys
from pathlib import Path
from typing import Any, Literal

from lib.agents.harness_settings import scoped_allow
from lib.counts_only.block_shapes import ManagedSettingsBlock, ManagedSource

ManagedState = Literal["not_read", "found", "absent", "unreadable"]
SourceKind = Literal["file", "drop_in", "plist", "registry_hklm", "registry_hkcu"]

OVERRIDE_ENV = "ASSESS_MANAGED_SETTINGS_DIR"
_FILE_NAME = "managed-settings.json"
_DROP_IN = "managed-settings.d"
_DIRS = {
    "darwin": Path("/Library/Application Support/ClaudeCode"),
    "linux": Path("/etc/claude-code"),
    "win32": Path("C:/Program Files/ClaudeCode"),
}
_PLIST_DIR = Path("/Library/Managed Preferences")
_PLIST_NAME = "com.anthropic.claudecode.plist"
_REGISTRY_KEY = r"SOFTWARE\Policies\ClaudeCode"


def not_read() -> ManagedSettingsBlock:
    """The block when the opt-in is off: nothing on the machine was read."""
    return {"state": "not_read", "sources": []}



def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _source(kind: SourceKind, data: Any) -> ManagedSource:
    """Count the guardrails in one parsed source; ``parse_ok`` false when it is not an object."""
    entry: ManagedSource = {"kind": kind, "parse_ok": isinstance(data, dict), "allow_count": 0,
                            "deny_count": 0, "ask_count": 0, "hook_events": 0}
    if not isinstance(data, dict):
        return entry
    permissions = data.get("permissions")
    if isinstance(permissions, dict):
        entry["allow_count"] = scoped_allow(permissions.get("allow"))
        entry["deny_count"] = _count(permissions.get("deny"))
        entry["ask_count"] = _count(permissions.get("ask"))
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        entry["hook_events"] = len(hooks)
    return entry


_UNREAD = object()


def _json_file(path: Path) -> Any:
    """The parsed file, None when it does not exist, ``_UNREAD`` when it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return _UNREAD
    try:
        return json.loads(text)
    except ValueError:
        return _UNREAD


def _file_sources(directory: Path) -> list[ManagedSource]:
    out: list[ManagedSource] = []
    data = _json_file(directory / _FILE_NAME)
    if data is not None:
        out.append(_source("file", None if data is _UNREAD else data))
    drop_in = directory / _DROP_IN
    try:
        names = (sorted(p for p in drop_in.iterdir() if p.suffix == ".json" and p.is_file())
                 if drop_in.is_dir() else [])
    except OSError:
        return [*out, _source("drop_in", None)]
    for path in names:
        part = _json_file(path)
        if part is not None:
            out.append(_source("drop_in", None if part is _UNREAD else part))
    return out


def _plist_sources() -> list[ManagedSource]:
    out: list[ManagedSource] = []
    user = os.environ.get("USER", "")
    for path in [_PLIST_DIR / _PLIST_NAME, *([_PLIST_DIR / user / _PLIST_NAME] if user else [])]:
        try:
            with path.open("rb") as handle:
                data: Any = plistlib.load(handle)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, plistlib.InvalidFileException):
            data = None
        out.append(_source("plist", data))
    return out


def _registry_sources() -> list[ManagedSource]:  # pragma: no cover - Windows only
    winreg: Any = importlib.import_module("winreg")  # Any: typeshed gates it on win32

    out: list[ManagedSource] = []
    hives: tuple[tuple[SourceKind, Any], ...] = (
        ("registry_hklm", winreg.HKEY_LOCAL_MACHINE), ("registry_hkcu", winreg.HKEY_CURRENT_USER))
    for kind, hive in hives:
        try:
            with winreg.OpenKey(hive, _REGISTRY_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "Settings")
        except FileNotFoundError:
            continue
        except OSError:
            out.append(_source(kind, None))
            continue
        try:
            data: Any = json.loads(str(value))
        except ValueError:
            data = None
        out.append(_source(kind, data))
    return out


def read_managed_settings(platform: str | None = None) -> ManagedSettingsBlock:
    """Read every managed source for ``platform`` (default ``sys.platform``); see the module doc."""
    platform = platform or sys.platform
    override = os.environ.get(OVERRIDE_ENV)
    try:
        if override:
            sources = _file_sources(Path(override))
        else:
            directory = _DIRS.get("linux" if platform.startswith("linux") else platform)
            sources = _file_sources(directory) if directory is not None else []
            if platform == "darwin":
                sources += _plist_sources()
            elif platform == "win32":  # pragma: no cover - Windows only
                sources += _registry_sources()
    except Exception:  # noqa: BLE001 - a reader that cannot run reads as unreadable, never raises
        return {"state": "unreadable", "sources": [], "reason": "managed_settings_unreadable"}
    if not sources:
        return {"state": "absent", "sources": []}
    if any(not s["parse_ok"] for s in sources):
        return {"state": "unreadable", "sources": sources, "reason": "managed_settings_unreadable"}
    return {"state": "found", "sources": sources}


def managed_permissions(block: ManagedSettingsBlock) -> bool:
    """True when a source that was read lists at least one permission entry."""
    return any(s["allow_count"] + s["deny_count"] + s["ask_count"] > 0
               for s in block["sources"] if s["parse_ok"])
