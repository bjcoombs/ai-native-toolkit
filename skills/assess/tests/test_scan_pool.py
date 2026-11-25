"""Tests for the bounded scan pool (lib/scan_pool.py, #1381)."""
from __future__ import annotations

import argparse
import threading
import time

import pytest

from lib import scan_pool as pool


def test_default_jobs_is_the_cpu_count_capped(monkeypatch):
    monkeypatch.setattr(pool.os, "cpu_count", lambda: 64)
    assert pool.default_jobs() == pool.JOBS_CAP
    monkeypatch.setattr(pool.os, "cpu_count", lambda: 3)
    assert pool.default_jobs() == 3
    monkeypatch.setattr(pool.os, "cpu_count", lambda: None)
    assert pool.default_jobs() == 1


@pytest.mark.parametrize("text", ["0", "-2", "two", "1.5"])
def test_jobs_below_one_or_not_a_number_is_a_usage_error(text):
    parser = argparse.ArgumentParser()
    pool.add_jobs_argument(parser)
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["--jobs", text])
    assert raised.value.code == 2
    assert parser.parse_args(["--jobs", "3"]).jobs == 3
    assert parser.parse_args([]).jobs == pool.default_jobs()


def test_dependencies_follow_the_earlier_producer_of_each_read():
    deps = pool.dependencies(
        [(), ("repo_root",), ("a",), ("b1", "a"), ("x",)],
        [("a",), ("b", "b1", "b2"), ("c",), ("d",), ("e",)],
    )
    assert deps == [frozenset(), frozenset(), frozenset({0}), frozenset({0, 1}), frozenset()]


def test_restore_order_moves_added_keys_into_group_order():
    d = {"before": 0, "c": 3, "a": 1, "b2": 2, "b1": 2}
    pool.restore_order(d, [["a"], ["b1", "b2"], ["c"]])
    assert list(d) == ["before", "a", "b1", "b2", "c"]


def _run(count, deps, jobs, work, fail=frozenset()):
    """``run_ordered`` over ``work[i]()`` calls; returns the finish order and the threads used."""
    finished: list[int] = []
    threads: set[str] = set()

    def prepare(i):
        if i in fail:
            raise ValueError(f"prepare {i}")

        def call():
            threads.add(threading.current_thread().name)
            return work[i]()
        return call

    pool.run_ordered(count, deps, prepare, lambda i, r: finished.append(i), jobs)
    return finished, threads


def test_jobs_one_runs_in_index_order_on_the_calling_thread():
    finished, threads = _run(3, [frozenset()] * 3, 1, [lambda: None] * 3)
    assert finished == [0, 1, 2]
    assert threads == {threading.current_thread().name}


def test_independent_entries_overlap_and_a_dependent_waits():
    """Two independent sleepers run together; the dependent one starts after its producer."""
    started: dict[int, float] = {}
    ended: dict[int, float] = {}

    def sleeper(i, seconds):
        def run():
            started[i] = time.perf_counter()
            time.sleep(seconds)
            ended[i] = time.perf_counter()
        return run

    work = [sleeper(0, 0.3), sleeper(1, 0.05), sleeper(2, 0.01)]
    finished, threads = _run(3, [frozenset(), frozenset(), frozenset({0})], 4, work)
    assert sorted(finished) == [0, 1, 2]
    assert finished.index(1) < finished.index(0) < finished.index(2)  # completion order
    assert started[1] < ended[0]  # 1 did not wait for 0
    assert started[2] >= ended[0]  # 2 waited for the entry it reads
    assert all(name.startswith("scan") for name in threads)


def test_the_earliest_failure_is_raised_after_the_entries_before_it_ran():
    """Entry 3 fails first in time; entry 1 (slow) still runs and fails, so its error is the
    one raised, as a jobs-1 run would raise it; entries after the earliest failure never start."""
    ran: list[int] = []

    def ok(i, seconds=0.0):
        def run():
            time.sleep(seconds)
            ran.append(i)
        return run

    def boom(i, seconds=0.0):
        def run():
            time.sleep(seconds)
            raise RuntimeError(f"entry {i}")
        return run

    work = [ok(0), boom(1, 0.2), ok(2, 0.1), boom(3), ok(4)]
    # 4 becomes ready when 2 finishes, after 3 has failed, so it is never started
    deps = [frozenset(), frozenset(), frozenset(), frozenset(), frozenset({2})]
    with pytest.raises(RuntimeError, match="entry 1"):
        _run(5, deps, 4, work)
    assert 4 not in ran and {0, 2} <= set(ran)


def test_a_prepare_failure_is_raised_in_index_order():
    work = [lambda: None] * 3
    with pytest.raises(ValueError, match="prepare 1"):
        _run(3, [frozenset()] * 3, 2, work, fail=frozenset({1, 2}))


def test_fleet_passes_scan_jobs_to_each_repository_scan(tmp_path):
    """#1381: /fleet --scan-jobs reaches assess_signals --jobs; unset, the child's default."""
    from lib.fleet.fleet_cli_args import parse_args
    from lib.fleet.runner import ScanOptions, signals_command

    command = signals_command(tmp_path / "c", tmp_path / "o.json", ScanOptions(scan_jobs=3))
    assert command[-2:] == ["--jobs", "3"]
    assert "--jobs" not in signals_command(tmp_path / "c", tmp_path / "o.json", ScanOptions())
    out = str(tmp_path / "out")
    assert parse_args([str(tmp_path), "--out", out, "--scan-jobs", "2"]).scan_jobs == 2
    assert parse_args([str(tmp_path), "--out", out]).scan_jobs is None
    with pytest.raises(SystemExit):
        parse_args([str(tmp_path), "--out", out, "--scan-jobs", "0"])
