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


@pytest.mark.parametrize(("allow", "count", "encoded"), [
    (["Bash(*)"], 0, False),
    (["Bash", "Bash( * )"], 0, False),
    (["Bash(*)", "Bash(npm test)"], 1, True),
])
def test_a_broad_managed_allow_is_not_counted(tmp_path, monkeypatch, allow, count,
                                              encoded) -> None:
    """A managed allow that lets agents run any shell command is not an encoded permission (#1621)."""
    directory = _point(monkeypatch, tmp_path)
    (directory / "managed-settings.json").write_text(
        json.dumps({"permissions": {"allow": allow}}), encoding="utf-8")
    block = ms.read_managed_settings()
    assert [s["allow_count"] for s in block["sources"]] == [count]
    assert ms.managed_permissions(block) is encoded


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_a_source_that_cannot_be_read_reads_unreadable(tmp_path, monkeypatch, text) -> None:
    directory = _point(monkeypatch, tmp_path)
    (directory / "managed-settings.json").write_text(text, encoding="utf-8")
    block = ms.read_managed_settings()
    assert block["state"] == "unreadable"
    assert block["reason"] == "managed_settings_unreadable"
    assert ms.managed_permissions(block) is False


def test_a_directory_named_like_json_in_the_drop_in_is_skipped(tmp_path, monkeypatch) -> None:
    directory = _point(monkeypatch, tmp_path)
    (directory / "managed-settings.json").write_text(json.dumps(DENY), encoding="utf-8")
    (directory / "managed-settings.d" / "old.json").mkdir(parents=True)
    block = ms.read_managed_settings()
    assert block["state"] == "found"
    assert [s["kind"] for s in block["sources"]] == ["file"]


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


@pytest.mark.parametrize(("text", "expected"), [
    ("[agent_ops]\nmanaged_settings = true\n", True),
    ("[agent_ops]\nmanaged_settings = \"yes\"\n", False),
    ("managed_settings = true\n", False),
    ("", False),
])
def test_the_config_opt_in_needs_a_literal_true(tmp_path, text, expected) -> None:
    from lib.assess_config import load_managed_settings_opt_in
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(text, encoding="utf-8")
    assert load_managed_settings_opt_in(tmp_path) is expected


def test_the_signals_run_reads_managed_settings_only_when_asked(tmp_path, monkeypatch) -> None:
    """assess_signals --managed-settings: the stamp says so and agent_ops reads the machine;
    the target's own config never turns it on (#1153)."""
    from lib.run import signals
    directory = _point(monkeypatch, tmp_path / "managed")
    directory.mkdir()
    (directory / "managed-settings.json").write_text(json.dumps(DENY), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".assess").mkdir(parents=True)
    (repo / ".assess" / "config.toml").write_text("[agent_ops]\nmanaged_settings = true\n",
                                                  encoding="utf-8")
    off = signals.build_document(repo, ["agent_ops"])
    assert off["stamp"]["managed_settings"] is False
    assert off["scans"]["agent_ops"]["managed"]["state"] == "not_read"
    on = signals.build_document(repo, ["agent_ops"], managed_settings=True)
    assert on["stamp"]["managed_settings"] is True
    assert on["scans"]["agent_ops"]["summary"]["permissions_source"] == "managed"


@pytest.mark.parametrize(("argv", "config", "expected"), [
    ([], "", False),
    (["--managed-settings"], "", True),
    ([], "[agent_ops]\nmanaged_settings = true\n", True),
])
def test_assess_core_takes_the_flag_or_the_config_key(tmp_path, monkeypatch, argv, config,
                                                      expected) -> None:
    """/assess reads the machine under --managed-settings or the single-repository key."""
    import assess_core
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(config, encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_run(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {"diff": {}}

    monkeypatch.setattr(assess_core, "build_and_publish_run", fake_run)
    monkeypatch.setattr(assess_core, "apply_counts_only", lambda ctx, parsed: None)
    assert assess_core.main([str(tmp_path), *argv]) == 0
    assert seen["managed_settings"] is expected
