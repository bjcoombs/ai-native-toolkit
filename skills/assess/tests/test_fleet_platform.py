"""Tests for the fleet platform pass, fleet.py --platform (issue #714): lib/fleet_platform.py.

Unit tests drive ``run_platform`` with a fake child; the end-to-end tests run
the real command over a repository with a github.com remote and a ``gh`` shell
stub on PATH, so nothing reaches the network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest

from lib import fleet_platform
from lib.fleet import SCAN_TIMEOUT, STATUS_FAILED, STATUS_OK, FleetRow, ScanOptions, Target
from lib.fleet_platform import failure_reason, join, platform_scans, run_platform
from lib.fleet_schema import build_schema, build_stamp_schema
from lib.run_context_types import JsonDict
from lib.signals import NETWORK_REASON, PLATFORM_SCANS
from test_fleet import make_repo
from test_fleet_cli import fleet_cli


def _row(repo: str, status: str = STATUS_OK) -> FleetRow:
    ok = status == STATUS_OK
    return {"fleet_row_version": 2, "repo": repo, "group": repo.rsplit("/", 1)[0], "status": status,
            "reason": None if ok else "scan_error", "signals_schema_version": 1 if ok else None,
            "scans": {"agent_ops": {"available": True},
                      "review_reality": {"available": False, "reason": "network"}} if ok else None,
            "active": True if ok else None}


TARGETS = [Target("g/a", Path("/nowhere/a")), Target("g/b", Path("/nowhere/b")),
           Target("g/c", Path("/nowhere/c"))]
BLOCK: JsonDict = {"available": True, "merged_count": 3}


def test_platform_scans_is_the_explicit_list_narrowed_by_scans():
    assert PLATFORM_SCANS == ("review_reality", "delivery")
    assert platform_scans(None) == ["review_reality", "delivery"]
    assert platform_scans(["agent_ops", "review_reality"]) == ["review_reality"]
    assert platform_scans(["delivery"]) == ["delivery"]
    assert platform_scans(["agent_ops"]) == []


def test_ok_rows_get_the_platform_blocks_and_failed_rows_are_left_alone():
    seen: list[tuple[str, ScanOptions, str]] = []

    def run(target: Target, _scratch: Path, options: ScanOptions, suffix: str) -> JsonDict | str:
        seen.append((target.repo, options, suffix))
        assert options.cache_dir is not None and options.cache_dir.is_dir()
        return {"signals_schema_version": 1, "scans": {"review_reality": BLOCK}}

    rows = [_row("g/b"), _row("g/a"), _row("g/c", STATUS_FAILED)]
    out = run_platform(rows, TARGETS, ScanOptions(scans="agent_ops,review_reality", timeout=600),
                       jobs=2, timeout=7, names=["review_reality"], run=run)
    assert [r["repo"] for r in out] == ["g/a", "g/b", "g/c"]
    assert out[0]["scans"] == {"agent_ops": {"available": True}, "review_reality": BLOCK}
    assert out[2] == rows[2]
    assert sorted(repo for repo, _, _ in seen) == ["g/a", "g/b"]
    child = seen[0][1]
    assert (child.platform, child.scans, child.timeout) == (True, "review_reality", 7)
    assert seen[0][2] == ".platform.stderr"
    assert child.cache_dir is not None and not child.cache_dir.exists()  # removed with the pass


@pytest.mark.parametrize(("counts_only", "reason"), [
    (True, "platform_timeout"),
    (False, "platform_timeout: the platform pass gave no result for this repository"),
])
def test_a_child_that_times_out_leaves_the_row_ok_with_an_unavailable_block(counts_only, reason):
    out = run_platform([_row("g/a")], TARGETS, ScanOptions(counts_only=counts_only), jobs=1, timeout=1,
                       names=["review_reality"], run=lambda *_a: SCAN_TIMEOUT)
    assert out[0]["status"] == STATUS_OK and out[0]["reason"] is None
    assert out[0]["scans"] is not None
    assert out[0]["scans"]["review_reality"] == {"available": False, "reason": reason}


def test_failure_tokens_map_to_platform_codes():
    assert failure_reason("scan_exit_3").startswith("platform_exit_3: ")
    assert failure_reason("scan_error").startswith("platform_error: ")
    assert failure_reason("invalid_output").startswith("platform_invalid_output: ")


def test_a_child_that_raises_or_omits_a_block_is_unavailable():
    def boom(*_args: object) -> JsonDict | str:
        raise RuntimeError("x")

    out = run_platform([_row("g/a")], TARGETS, ScanOptions(), jobs=1, timeout=1,
                       names=["review_reality"], run=boom)
    assert out[0]["scans"] is not None
    assert out[0]["scans"]["review_reality"] == {"available": False, "reason": "platform_error"}
    out = run_platform([_row("g/a")], TARGETS, ScanOptions(), jobs=1, timeout=1, names=["review_reality"],
                       run=lambda *_a: {"signals_schema_version": 1, "scans": {}})
    assert out[0]["scans"] is not None
    assert out[0]["scans"]["review_reality"] == {"available": False, "reason": "platform_invalid_output"}


def test_after_runs_on_each_row_once_its_platform_read_is_joined():
    order: list[str] = []
    after: list[tuple[str, FleetRow]] = []

    def run(target: Target, *_args: object) -> JsonDict | str:
        order.append(f"read {target.repo}")
        return {"signals_schema_version": 1, "scans": {"review_reality": BLOCK}}

    def hook(target: Target, row: FleetRow) -> None:
        order.append(f"after {target.repo}")
        after.append((target.repo, row))

    run_platform([_row("g/a"), _row("g/c", STATUS_FAILED)], TARGETS, ScanOptions(), jobs=1, timeout=1,
                 names=["review_reality"], after=hook, run=run)
    assert order.index("read g/a") < order.index("after g/a")
    assert {repo for repo, _ in after} == {"g/a", "g/c"}
    joined = dict(after)["g/a"]["scans"]
    assert joined is not None and joined["review_reality"] == BLOCK


def test_no_names_reads_nothing():
    def run(*_args: object) -> JsonDict | str:
        raise AssertionError("no platform scan selected")

    rows = [_row("g/a")]
    assert run_platform(rows, TARGETS, ScanOptions(), jobs=1, timeout=1, names=[], run=run) == rows


def test_join_never_touches_status():
    failed = _row("g/c", STATUS_FAILED)
    assert join(failed, {"review_reality": BLOCK}) is failed


# --- the command -----------------------------------------------------------

GH_STUB = """#!/bin/sh
echo "$*" >> "$GH_STUB_DIR/log"
case "$1" in
  auth) exit 0 ;;
  pr) [ -f "$GH_STUB_DIR/hang" ] && sleep 20; echo '[]'; exit 0 ;;
esac
echo "gh: Not Found (HTTP 404)" >&2; exit 1
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir, data = tmp_path / "bin", tmp_path / "gh"
    bindir.mkdir()
    data.mkdir()
    (bindir / "gh").write_text(GH_STUB, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    (bindir / "glab").write_text("#!/bin/sh\necho \"$*\" >> \"$GH_STUB_DIR/glab\"\nexit 1\n", encoding="utf-8")
    (bindir / "glab").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_STUB_DIR", str(data))
    monkeypatch.chdir(tmp_path)
    make_repo(tmp_path / "root", "grp/svc", remote="https://github.com/grp/svc.git")
    return data


def _run(tmp_path: Path, *extra: str) -> tuple[list[FleetRow], JsonDict]:
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "out"), "--no-history",
                           "--no-diagrams", "--scans", "agent_ops,review_reality", *extra]) == 0
    rows = [json.loads(line) for line in (tmp_path / "out" / "repos.jsonl").read_text().splitlines()]
    stamp = json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))
    return rows, stamp


def _calls(data: Path, name: str = "log") -> list[str]:
    path = data / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_without_platform_no_gh_or_glab_call_is_made(world, tmp_path):
    rows, stamp = _run(tmp_path)
    assert _calls(world) == [] and _calls(world, "glab") == []
    assert rows[0]["scans"]["review_reality"] == {"available": False,
                                                  "reason": NETWORK_REASON.split(":", 1)[0]}
    assert stamp["platform"] is False


def test_platform_joins_the_read_and_stamps_the_run(world, tmp_path):
    rows, stamp = _run(tmp_path, "--platform")
    assert any(call.startswith("pr list --repo grp/svc") for call in _calls(world))
    assert rows[0]["status"] == "ok"
    block = rows[0]["scans"]["review_reality"]
    assert block["available"] is True and block["merged_count"] == 0
    assert rows[0]["scans"]["agent_ops"]["available"] is True
    assert stamp["platform"] is True
    jsonschema.validate(stamp, build_stamp_schema())
    jsonschema.validate(rows[0], build_schema())


def test_a_hanging_host_never_turns_the_row_into_scan_timeout(world, tmp_path):
    (world / "hang").write_text("", encoding="utf-8")
    rows, _ = _run(tmp_path, "--platform", "--platform-timeout", "4")
    assert rows[0]["status"] == "ok" and rows[0]["reason"] is None
    assert rows[0]["scans"]["review_reality"] == {"available": False, "reason": "platform_timeout"}
    assert rows[0]["scans"]["agent_ops"]["available"] is True
    jsonschema.validate(rows[0], build_schema())


def test_platform_settings_come_from_the_fleet_config_and_the_flag_wins(world, tmp_path, monkeypatch):
    seen: list[tuple[int, int]] = []

    def fake(rows, _targets, _options, *, jobs, timeout, names, after=None):
        seen.append((jobs, timeout))
        return rows

    monkeypatch.setattr(fleet_cli, "run_platform", fake)
    config = tmp_path / "fleet.toml"
    config.write_text("[platform]\njobs = 2\ntimeout = 90\n", encoding="utf-8")
    _run(tmp_path, "--platform", "--fleet-config", str(config))
    _run(tmp_path, "--platform", "--fleet-config", str(config), "--platform-jobs", "5")
    _run(tmp_path, "--platform")
    assert seen == [(2, 90), (5, 90), (fleet_platform.DEFAULT_PLATFORM_JOBS,
                                       fleet_platform.DEFAULT_PLATFORM_TIMEOUT)]


@pytest.mark.parametrize("argv", [["--platform-jobs", "2"], ["--platform", "--platform-timeout", "0"]])
def test_platform_flag_errors(world, tmp_path, argv):
    with pytest.raises(SystemExit) as caught:
        fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "out"), *argv])
    assert caught.value.code == 2


def test_delete_after_waits_for_the_platform_read(world, tmp_path, monkeypatch):
    order: list[str] = []
    real = fleet_cli.run_platform

    def spy(rows, targets, options, *, jobs, timeout, names, after=None):
        order.append("platform")
        assert all(t.checkout.exists() for t in targets)  # nothing deleted yet

        def hook(target, row):
            order.append("delete")
            assert after is not None
            after(target, row)
        return real(rows, targets, options, jobs=jobs, timeout=timeout, names=names, after=hook)

    monkeypatch.setattr(fleet_cli, "run_platform", spy)
    _run(tmp_path, "--platform", "--delete-after")
    assert order == ["platform", "delete"]


def test_platform_with_no_platform_scan_selected_is_not_stamped(world, tmp_path):
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "out"), "--no-history",
                           "--no-diagrams", "--scans", "agent_ops", "--platform"]) == 0
    stamp = json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))
    assert stamp["platform"] is False
    assert _calls(world) == []
