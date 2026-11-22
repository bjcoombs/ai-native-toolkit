"""Tests for the fleet platform pass, fleet.py --platform (issue #714): lib/fleet_platform.py.

Unit tests drive ``run_platform`` with a fake child; the end-to-end tests run
the real command over a repository with a github.com remote and a ``gh`` shell
stub on PATH, so nothing reaches the network.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest

from conftest import skip_without_posix_shell

from lib import fleet_platform
from lib.fleet import (
    SCAN_TIMEOUT,
    STATUS_FAILED,
    STATUS_OK,
    FleetRow,
    ScanOptions,
    Target,
    signals_command,
)
from lib.fleet_platform import failure_reason, join, platform_scans, run_platform
from lib.fleet_schema import build_schema, build_stamp_schema
from lib.merged_sample import PR_FIELDS
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
    assert PLATFORM_SCANS == ("review_reality", "delivery", "review_evidence", "review_automation",
                              "ci_checks")
    assert platform_scans(None) == list(PLATFORM_SCANS)
    # review_reality brings its companions review_automation and review_evidence (#696),
    # rebuilt by the pass; ci_checks and review_automation read the merged CI config (#796).
    assert platform_scans(["agent_ops", "review_reality"]) == [
        "review_reality", "review_evidence", "review_automation"]
    assert platform_scans(["ci_checks"]) == ["ci_checks"]
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
  pr) [ -f "$GH_STUB_DIR/hang" ] && sleep 20
      [ -f "$GH_STUB_DIR/refuse" ] && { echo "gh: HTTP 403" >&2; exit 1; }
      echo '[]'; exit 0 ;;
esac
echo "gh: Not Found (HTTP 404)" >&2; exit 1
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skip_without_posix_shell()
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
    rows = [json.loads(line) for line in (tmp_path / "out" / "repos.jsonl").read_text(encoding="utf-8").splitlines()]
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
    # --scans review_reality brings review_automation and review_evidence (#696), and the
    # platform pass rebuilt review_evidence from the measured block, not the network gap.
    assert rows[0]["scans"]["review_automation"]["present"] is False
    assert rows[0]["scans"]["review_evidence"].get("reason") != "network"
    assert stamp["platform"] is True
    jsonschema.validate(stamp, build_stamp_schema())
    jsonschema.validate(rows[0], build_schema())


def test_delivery_is_absent_offline_and_joined_by_the_platform_pass(world, tmp_path):
    """No ``--platform``: no ``delivery`` block at all, not a placeholder (#779); the pass adds it."""
    rows, _ = _run(tmp_path)
    assert "delivery" not in rows[0]["scans"]
    assert rows[0]["scans"]["review_reality"]["available"] is False
    jsonschema.validate(rows[0], build_schema())
    rows, _ = _run(tmp_path, "--scans", "agent_ops,review_reality,delivery", "--platform")
    delivery = rows[0]["scans"]["delivery"]
    assert delivery["available"] is True and delivery["merged_count"] == 0
    jsonschema.validate(rows[0], build_schema())


def test_a_refused_pr_list_gives_both_platform_blocks_no_access(world, tmp_path):
    """``gh pr list`` answering HTTP 403 reads as ``no_access``, as on GitLab (#779)."""
    (world / "refuse").write_text("", encoding="utf-8")
    rows, _ = _run(tmp_path, "--scans", "agent_ops,review_reality,delivery", "--platform")
    assert rows[0]["status"] == "ok"
    for name in ("review_reality", "delivery"):
        assert rows[0]["scans"][name] == {"available": False, "reason": "no_access"}, name


def test_scans_delivery_without_platform_is_rejected(world, tmp_path, capsys):
    """``--scans delivery`` alone would give no block and no reason (#779): exit 2."""
    with pytest.raises(SystemExit) as exc:
        fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "out"), "--scans", "delivery"])
    assert exc.value.code == 2
    assert "--platform" in capsys.readouterr().err
    assert _calls(world) == []


def test_the_offline_child_never_asks_for_a_platform_only_scan(tmp_path):
    offline = signals_command(tmp_path, tmp_path / "o.json", ScanOptions(scans="agent_ops,delivery"))
    assert offline[offline.index("--scans") + 1] == "agent_ops"
    online = signals_command(tmp_path, tmp_path / "o.json",
                             ScanOptions(scans="delivery", platform=True))
    assert online[online.index("--scans") + 1] == "delivery"


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


# --- no review or comment body reaches the response cache (#778) -------------

# Answers ``--json`` with that read's fields only, as gh does. Before every call it
# records each file under the watched directories that holds a body, so a body
# cached mid-pass (the cache directory is gone once the pass ends) is caught.
LEAK_STUB = """#!/bin/sh
grep -rl SECRET-BODY $LEAK_DIRS >> "$GH_STUB_DIR/leak.log" 2>/dev/null
echo "$*" >> "$GH_STUB_DIR/log"
case "$1" in auth) exit 0 ;; esac
case "$*" in
  "pr list --repo "*"--json mergedAt") cat "$GH_STUB_DIR/light-$4.json"; exit 0 ;;
  "pr list --repo "*) cat "$GH_STUB_DIR/heavy-$4.json"; exit 0 ;;
esac
echo "gh: Not Found (HTTP 404)" >&2; exit 1
"""


def _merged_prs(name: str) -> list[JsonDict]:
    now = datetime.now(timezone.utc)
    prs: list[JsonDict] = []
    for k in (3, 5, 9):
        created = now - timedelta(days=k)
        stamp = (created + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        prs.append({
            "number": k, "author": {"login": "alice"}, "mergedBy": {"login": "carol"},
            "createdAt": created.strftime("%Y-%m-%dT%H:%M:%SZ"), "mergedAt": stamp,
            "reviewDecision": "APPROVED",
            "comments": [{"author": {"login": "dave"}, "body": f"SECRET-BODY-c-{name}-{k}",
                          "createdAt": stamp}],
            "reviews": [{"author": {"login": "bob"}, "state": "APPROVED",
                         "body": f"SECRET-BODY-r-{name}-{k}", "submittedAt": stamp}],
        })
    return prs


def test_no_github_review_or_comment_body_is_written_to_disk(tmp_path, monkeypatch):
    skip_without_posix_shell()
    bindir, data, scratch, out = tmp_path / "bin", tmp_path / "gh", tmp_path / "tmp", tmp_path / "p1"
    for d in (bindir, data, scratch):
        d.mkdir()
    (bindir / "gh").write_text(LEAK_STUB, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    for name in ("gha", "ghb"):
        make_repo(tmp_path / "gp", f"org/{name}", remote=f"https://github.com/org/{name}.git")
        heavy = _merged_prs(name)
        (data / f"heavy-org/{name}.json").parent.mkdir(exist_ok=True)
        (data / f"heavy-org/{name}.json").write_text(json.dumps(heavy), encoding="utf-8")
        (data / f"light-org/{name}.json").parent.mkdir(exist_ok=True)
        (data / f"light-org/{name}.json").write_text(
            json.dumps([{"mergedAt": pr["mergedAt"]} for pr in heavy]), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_STUB_DIR", str(data))
    monkeypatch.setenv("LEAK_DIRS", f"{scratch} {out}")
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))  # the pass's cache directory
    monkeypatch.chdir(tmp_path)
    assert fleet_cli.main([str(tmp_path / "gp"), "--out", str(out), "--no-history", "--no-diagrams",
                           "--scans", "agent_ops,review_reality,delivery", "--platform",
                           "--platform-jobs", "1"]) == 0
    calls = _calls(data)
    for name in ("gha", "ghb"):
        assert any(c.startswith(f"pr list --repo org/{name} ") and c.endswith(PR_FIELDS)
                   for c in calls), name
    rows = [json.loads(line) for line in (out / "repos.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(r["scans"]["delivery"]["available"] is True for r in rows)
    assert all(r["scans"]["delivery"]["merged_count"] == 3 for r in rows)
    assert _calls(data, "leak.log") == []
    for root in (scratch, out):
        assert not [p for p in root.rglob("*") if p.is_file() and b"SECRET-BODY" in p.read_bytes()]
