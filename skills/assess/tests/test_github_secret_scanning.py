"""GitHub's secret scanning and push protection settings, read through gh (#1173)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.hosting.github_secret_scanning as gss
from lib.hosting.gh_cli import GhUnavailable, GithubRepo


def _fake(monkeypatch: pytest.MonkeyPatch, meta: Any) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(gss, "open_github", lambda root: GithubRepo("acme", "widget"))

    def api(path: str) -> Any:
        calls.append(path)
        return meta
    monkeypatch.setattr(gss, "gh_api", api)
    return calls


def _settings(scanning: str, push: str) -> dict[str, Any]:
    return {"security_and_analysis": {"secret_scanning": {"status": scanning},
                                      "secret_scanning_push_protection": {"status": push}}}


@pytest.mark.parametrize(("scanning", "push"), [("enabled", "enabled"), ("enabled", "disabled"),
                                                 ("disabled", "enabled"), ("disabled", "disabled")])
def test_each_setting_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scanning: str, push: str) -> None:
    calls = _fake(monkeypatch, _settings(scanning, push))
    block = gss.scan_github_secret_scanning(tmp_path)
    assert block == {"available": True, "secret_scanning": scanning == "enabled",
                     "push_protection": push == "enabled", "delegated_bypass": False}
    assert calls == ["repos/acme/widget"]


@pytest.mark.parametrize("meta", [{"security_and_analysis": None}, {}, []])
def test_an_unreadable_setting_is_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meta: Any) -> None:
    _fake(monkeypatch, meta)
    block = gss.scan_github_secret_scanning(tmp_path)
    assert block["available"] is False and block["reason"].startswith("settings_not_readable:")


def test_a_missing_setting_reads_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, {"security_and_analysis": {"dependabot_security_updates": {"status": "enabled"}}})
    block = gss.scan_github_secret_scanning(tmp_path)
    assert block == {"available": True, "secret_scanning": False, "push_protection": False,
                     "delegated_bypass": False}


def test_no_github_remote_or_gh_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(root: Path) -> GithubRepo:
        raise GhUnavailable("no_remote: no github.com remote")
    monkeypatch.setattr(gss, "open_github", refuse)
    block = gss.scan_github_secret_scanning(tmp_path)
    assert block["available"] is False and block["reason"].startswith("no_remote")


def test_an_api_error_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gss, "open_github", lambda root: GithubRepo("acme", "widget"))

    def fail(path: str) -> Any:
        raise GhUnavailable("api_error: 502")
    monkeypatch.setattr(gss, "gh_api", fail)
    assert gss.scan_github_secret_scanning(tmp_path)["available"] is False


def test_delegated_bypass_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _settings("enabled", "enabled")
    meta["security_and_analysis"]["secret_scanning_delegated_bypass"] = {"status": "enabled"}
    _fake(monkeypatch, meta)
    assert gss.scan_github_secret_scanning(tmp_path)["delegated_bypass"] is True
