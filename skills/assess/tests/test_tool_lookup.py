"""Tool lookup for Windows runs: rg resolution and the no-uv install hint (#711)."""
from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest

from lib import headless_layers as hl
from lib import promissory_markers as pm
from lib.counts_only import reason_code
from lib.tool_lookup import (
    RG_MISSING_REASON,
    find_rg,
    missing_packages,
    pip_hint,
    warn_missing_packages,
    winget_rg,
)


def _no_which(_name: str) -> str | None:
    return None


def _winget_tree(tmp_path: Path, *versions: str) -> Path:
    """A fake ``%LOCALAPPDATA%`` with ripgrep installed by winget at user scope."""
    packages = tmp_path / "Microsoft" / "WinGet" / "Packages"
    for v in versions:
        exe = (packages / "BurntSushi.ripgrep.MSVC_Microsoft.Winget.Source_8wekyb3d8bbwe"
               / f"ripgrep-{v}-x86_64-pc-windows-msvc" / "rg.exe")
        exe.parent.mkdir(parents=True)
        exe.write_text("")
    return tmp_path


def test_rg_path_wins_over_path_and_winget(tmp_path: Path) -> None:
    rg = tmp_path / "custom" / "rg"
    rg.parent.mkdir()
    rg.write_text("")
    local = _winget_tree(tmp_path / "local", "14.1.0")
    env = {"RG_PATH": str(rg), "LOCALAPPDATA": str(local)}
    assert find_rg(env, which=lambda _: "/usr/bin/rg", windows=True) == str(rg)


def test_rg_path_naming_no_file_falls_through_to_path(tmp_path: Path) -> None:
    env = {"RG_PATH": str(tmp_path / "missing" / "rg")}
    assert find_rg(env, which=lambda _: "/usr/bin/rg", windows=False) == "/usr/bin/rg"


def test_path_lookup_used_before_winget(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(_winget_tree(tmp_path, "14.1.0"))}
    assert find_rg(env, which=lambda _: r"C:\tools\rg.exe", windows=True) == r"C:\tools\rg.exe"


def test_winget_package_folder_found_when_not_on_path(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(_winget_tree(tmp_path, "14.1.0"))}
    found = find_rg(env, which=_no_which, windows=True)
    assert found is not None and found.endswith("rg.exe")
    assert "ripgrep-14.1.0" in found


def test_winget_pick_is_deterministic_across_versions(tmp_path: Path) -> None:
    local = _winget_tree(tmp_path, "14.1.0", "13.0.0")
    found = winget_rg(str(local))
    assert found is not None and "ripgrep-14.1.0" in found


def test_winget_folder_not_searched_off_windows(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(_winget_tree(tmp_path, "14.1.0"))}
    assert find_rg(env, which=_no_which, windows=False) is None


def test_none_found_without_localappdata_or_packages(tmp_path: Path) -> None:
    assert find_rg({}, which=_no_which, windows=True) is None
    assert winget_rg(str(tmp_path)) is None  # no WinGet/Packages folder
    assert winget_rg(None) is None


def test_missing_reason_maps_to_no_rg() -> None:
    assert "rg not on PATH" in RG_MISSING_REASON  # headless_layers matches this substring
    marker = hl.promissory_markers({"available": False, "reason": RG_MISSING_REASON})
    assert reason_code(marker["reason"]) == "no_rg"
    for lookup in ("RG_PATH", "PATH", "WinGet"):
        assert lookup in RG_MISSING_REASON


def test_scan_reports_missing_reason_when_rg_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pm, "find_rg", lambda: str(tmp_path / "no-such-rg"))
    scan = pm.scan_promissory_markers(tmp_path)
    assert scan.available is False and scan.reason == RG_MISSING_REASON


@pytest.mark.skipif(shutil.which("rg") is None, reason="needs ripgrep")
def test_scan_runs_the_resolved_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rg = shutil.which("rg")
    assert rg is not None
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text("# TODO: later\n")
    calls: list[str] = []
    real_run = pm._run

    def spy(cmd: list[str], cwd: Path) -> str:
        calls.append(cmd[0])
        return real_run(cmd, cwd)

    monkeypatch.setattr(pm, "find_rg", lambda: rg)
    monkeypatch.setattr(pm, "_run", spy)
    pm.scan_promissory_markers(tmp_path)
    assert rg in calls


def test_missing_packages_names_pip_requirements() -> None:
    absent = {"grimp", "lizard"}

    def spec(name: str) -> object | None:
        return None if name in absent else object()

    assert missing_packages(find_spec=spec) == ["grimp", "lizard==1.23.0"]
    assert missing_packages(find_spec=lambda _: object()) == []


def test_missing_packages_treats_a_broken_spec_as_missing() -> None:
    def broken(_name: str) -> object | None:
        raise ValueError("__spec__ is None")

    assert len(missing_packages(find_spec=broken)) == 3


def test_pip_hint_gives_the_no_uv_install_line() -> None:
    hint = pip_hint(["grimp", "lizard==1.23.0"])
    assert "grimp, lizard not installed" in hint
    assert hint.endswith("python -m pip install --user grimp lizard==1.23.0")


def test_warn_prints_only_when_something_is_missing() -> None:
    out = io.StringIO()
    assert warn_missing_packages(out, find_spec=lambda _: object()) is False
    assert out.getvalue() == ""
    assert warn_missing_packages(out, find_spec=lambda _: None) is True
    assert "python -m pip install --user networkx grimp lizard==1.23.0" in out.getvalue()


def test_signals_cli_prints_the_hint_when_a_package_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import assess_signals
    from lib import tool_lookup

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setattr(tool_lookup, "missing_packages", lambda **_: ["lizard==1.23.0"])
    out = tmp_path / "signals.json"
    assert assess_signals.main([str(repo), "--out", str(out), "--scans", "promissory_markers"]) == 0
    assert "python -m pip install --user lizard==1.23.0" in capsys.readouterr().err
    assert out.is_file()  # advisory: the run still completes
