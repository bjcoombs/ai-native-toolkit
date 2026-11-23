"""The managed settings reader (lib/agents/managed_settings.py, #1153)."""
from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest

import lib.agents.managed_settings as ms

DENY = {"permissions": {"deny": ["Bash(git push origin main)"]}, "hooks": {"PreToolUse": []}}


def _point(monkeypatch: pytest.MonkeyPatch, directory: Path) -> Path:
    monkeypatch.setenv(ms.OVERRIDE_ENV, str(directory))
    return directory


def test_no_source_reads_absent(tmp_path, monkeypatch) -> None:
    _point(monkeypatch, tmp_path / "missing")
    assert ms.read_managed_settings() == {"state": "absent", "sources": []}


def test_file_and_drop_in_are_counted(tmp_path, monkeypatch) -> None:
    directory = _point(monkeypatch, tmp_path)
    (directory / "managed-settings.json").write_text(json.dumps(DENY), encoding="utf-8")
    (directory / "managed-settings.d").mkdir()
    (directory / "managed-settings.d" / "10-team.json").write_text(
        json.dumps({"permissions": {"ask": ["Bash(rm *)"], "allow": ["Read"]}}), encoding="utf-8")
    (directory / "managed-settings.d" / "notes.txt").write_text("ignored", encoding="utf-8")
    block = ms.read_managed_settings()
    assert block["state"] == "found"
    assert [(s["kind"], s["deny_count"], s["ask_count"], s["allow_count"], s["hook_events"])
            for s in block["sources"]] == [("file", 1, 0, 0, 1), ("drop_in", 0, 1, 1, 0)]
    assert ms.managed_permissions(block) is True


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_a_source_that_cannot_be_read_reads_unreadable(tmp_path, monkeypatch, text) -> None:
    directory = _point(monkeypatch, tmp_path)
    (directory / "managed-settings.json").write_text(text, encoding="utf-8")
    block = ms.read_managed_settings()
    assert block["state"] == "unreadable"
    assert block["reason"] == "managed_settings_unreadable"
    assert ms.managed_permissions(block) is False


def test_an_unreadable_source_beside_a_rule_still_credits(tmp_path, monkeypatch) -> None:
    directory = _point(monkeypatch, tmp_path)
    (directory / "managed-settings.json").write_text(json.dumps(DENY), encoding="utf-8")
    (directory / "managed-settings.d").mkdir()
    (directory / "managed-settings.d" / "broken.json").write_text("{", encoding="utf-8")
    block = ms.read_managed_settings()
    assert block["state"] == "unreadable"
    assert ms.managed_permissions(block) is True


def test_platform_locations(tmp_path, monkeypatch) -> None:
    """Without the override, the platform's own directory, and on macOS the plist, are read."""
    monkeypatch.delenv(ms.OVERRIDE_ENV, raising=False)
    linux = tmp_path / "etc"
    linux.mkdir()
    (linux / "managed-settings.json").write_text(json.dumps(DENY), encoding="utf-8")
    plists = tmp_path / "prefs"
    (plists / "alice").mkdir(parents=True)
    (plists / "alice" / ms._PLIST_NAME).write_bytes(plistlib.dumps(DENY))
    monkeypatch.setattr(ms, "_DIRS", {"linux": linux, "darwin": tmp_path / "none"})
    monkeypatch.setattr(ms, "_PLIST_DIR", plists)
    monkeypatch.setenv("USER", "alice")
    assert [s["kind"] for s in ms.read_managed_settings("linux")["sources"]] == ["file"]
    darwin = ms.read_managed_settings("darwin")
    assert [s["kind"] for s in darwin["sources"]] == ["plist"]
    assert darwin["state"] == "found"
    assert ms.read_managed_settings("sunos5") == {"state": "absent", "sources": []}


def test_a_bad_plist_reads_unreadable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(ms.OVERRIDE_ENV, raising=False)
    (tmp_path / ms._PLIST_NAME).write_bytes(b"not a plist")
    monkeypatch.setattr(ms, "_DIRS", {"darwin": tmp_path / "none"})
    monkeypatch.setattr(ms, "_PLIST_DIR", tmp_path)
    assert ms.read_managed_settings("darwin")["state"] == "unreadable"


def test_a_reader_that_fails_never_raises(tmp_path, monkeypatch) -> None:
    _point(monkeypatch, tmp_path)

    def boom(_: Path) -> list[ms.ManagedSource]:
        raise RuntimeError("boom")

    monkeypatch.setattr(ms, "_file_sources", boom)
    assert ms.read_managed_settings()["state"] == "unreadable"
