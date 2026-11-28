"""A git call that times out or cannot start is retried, then named (#1686).

``test_scan_output_is_byte_identical_across_runs`` failed once in a full suite
run at a load average above 15 and passed seven times after. The scan's only
input that depends on the machine rather than the repository is whether its
two git calls finish inside ``GIT_TIMEOUT_SECONDS`` and can start at all. Before
#1686 a timeout or a refused ``fork`` read as "not a git repository" or "no
commit history", so two scans of one repository could serialize differently.
These tests pin the fix: one such failure is retried and leaves the summary
byte-identical, and a failure on every attempt degrades with a reason naming it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import lib.keyhole.accretion_ratchet as ar
from lib.keyhole.accretion_ratchet import scan_accretion_ratchet
from test_accretion_ratchet import _Clock, _commit, _init_repo, _lines


def _flaky_run(failures: dict[str, BaseException]):
    """A ``subprocess.run`` that raises ``failures[subcommand]`` once per git subcommand."""
    real = subprocess.run
    calls: list[str] = []

    def run(cmd, *a, **k):
        calls.append(cmd[3])
        if cmd[3] in failures:
            raise failures.pop(cmd[3])
        return real(cmd, *a, **k)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _ratchet_repo(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
    clock = _Clock()
    for i in range(1, 5):
        _commit(repo, "a.py", _lines(100 * i), clock)
    return repo


def test_a_git_log_timeout_is_read_again_and_matches_an_unloaded_scan(
    tmp_path: Path, monkeypatch
) -> None:
    """One timed-out ``git log`` is retried, so load does not change the summary.

    Under heavy load the history read can pass ``GIT_TIMEOUT_SECONDS``. Before
    #1686 that read as an empty map and the scan reported "no commit history",
    so two scans of one repository differed. The second attempt succeeds here and
    the summary is byte-identical to a scan that never timed out.
    """
    repo = _ratchet_repo(tmp_path)
    clean = json.dumps(scan_accretion_ratchet(repo).summary(), sort_keys=True)

    timeout = subprocess.TimeoutExpired(["git", "log"], ar.GIT_TIMEOUT_SECONDS)
    run = _flaky_run({
        "rev-parse": BlockingIOError(35, "Resource temporarily unavailable"),
        "log": timeout,
    })
    monkeypatch.setattr(ar.subprocess, "run", run)
    loaded = scan_accretion_ratchet(repo)

    assert json.dumps(loaded.summary(), sort_keys=True) == clean
    # rev-parse failed to start once, git log timed out once: four calls in all.
    assert run.calls == ["rev-parse", "rev-parse", "log", "log"]


def test_a_git_log_that_keeps_timing_out_names_the_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    """A read that times out on every attempt degrades with a reason naming it.

    It never reads as "no commit history": that reason is a fact about the
    repository, and a timeout is a fact about the machine.
    """
    repo = _ratchet_repo(tmp_path)
    real = subprocess.run

    def run(cmd, *a, **k):
        if cmd[3] == "log":
            raise subprocess.TimeoutExpired(cmd, ar.GIT_TIMEOUT_SECONDS)
        return real(cmd, *a, **k)

    monkeypatch.setattr(ar.subprocess, "run", run)
    scan = scan_accretion_ratchet(repo)
    assert scan.available is False
    assert scan.reason == (
        f"GitUnavailable: git log timed out after {ar.GIT_TIMEOUT_SECONDS}s "
        f"on {ar.GIT_ATTEMPTS} attempts"
    )


def test_a_rev_parse_that_cannot_start_is_not_read_as_not_a_repository(
    tmp_path: Path, monkeypatch
) -> None:
    """A ``fork`` refused on every attempt degrades with that reason, not "not a git repository"."""
    repo = _ratchet_repo(tmp_path)

    def run(cmd, *a, **k):
        raise BlockingIOError(35, "Resource temporarily unavailable")

    monkeypatch.setattr(ar.subprocess, "run", run)
    scan = scan_accretion_ratchet(repo)
    assert scan.available is False
    assert scan.reason.startswith("GitUnavailable: git rev-parse could not start:")
